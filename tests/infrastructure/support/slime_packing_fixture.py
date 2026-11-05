"""Numerical GRPO loss/gradient/update equivalence with real Slime reducers.

This is a small causal model, not a claim of 27B throughput or convergence.
Run with the native Slime environment and an explicit scratch directory.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path

import torch
from megatron.core import parallel_state
from slime.backends.megatron_utils.cp_utils import get_sum_of_sample_mean
from slime.utils.ppo_utils import compute_policy_loss
from torch import nn

from behave.train.slime_rollout import coalesce_payloads


class CausalModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(96, 16, dtype=torch.float64)
        self.position = nn.Embedding(128, 16, dtype=torch.float64)
        self.attention = nn.TransformerEncoderLayer(16, 2, 32, dropout=0., batch_first=True, dtype=torch.float64)
        self.head = nn.Linear(16, 96, bias=False, dtype=torch.float64)

    def forward(self, ids):
        positions = torch.arange(len(ids), device=ids.device)
        hidden = (self.embedding(ids) + self.position(positions))[None]
        causal = torch.ones(len(ids), len(ids), device=ids.device, dtype=torch.bool).triu(1)
        return self.head(self.attention(hidden, src_mask=causal))[0]


def payload(turn, inputs, outputs, advantage, model, device):
    ids = torch.tensor(inputs + outputs, device=device)
    with torch.no_grad():
        logits = model(ids[:-1]).log_softmax(-1)
        probabilities = logits.gather(1, ids[1:, None]).squeeze(1)[len(inputs) - 1:]
        offsets = torch.tensor([-.4, -.1, .1, .4], device=device, dtype=probabilities.dtype)
        old = (probabilities + offsets[torch.arange(len(outputs), device=device) % 4]).tolist()
    return {"index": turn, "rollout_id": 0, "tokens": inputs + outputs,
        "response_length": len(outputs), "loss_mask": [1] * len(outputs),
        "rollout_log_probs": old, "reward": advantage,
        "metadata": {"turn": turn, "request": {"id": turn}, "response": {"id": turn}}}


def train_step(model, data, device):
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=.1)
    denominator = sum(sum(item["loss_mask"]) for item in data)
    total = torch.zeros((), device=device, dtype=torch.float64)
    for item in data:
        ids = torch.tensor(item["tokens"], device=device)
        prompt = len(ids) - item["response_length"]
        logits = model(ids[:-1]).log_softmax(-1)
        probabilities = logits.gather(1, ids[1:, None]).squeeze(1)[prompt - 1:]
        old = torch.tensor(item["rollout_log_probs"], device=device, dtype=torch.float64)
        advantage = torch.full_like(probabilities, item["reward"])
        losses, _ = compute_policy_loss(old - probabilities, advantage, .2, .2)
        mask = torch.tensor(item["loss_mask"], device=device, dtype=torch.float64)
        reduce = get_sum_of_sample_mean([len(ids)], [len(mask)], [mask],
            [torch.tensor(denominator, device=device, dtype=torch.float64)])
        partial = reduce(losses)
        partial.backward()
        total += partial.detach()
    gradient = torch.cat([parameter.grad.flatten() for parameter in model.parameters()])
    assert gradient.norm() > 0
    optimizer.step()
    parameters = torch.cat([parameter.detach().flatten() for parameter in model.parameters()])
    return total, gradient, parameters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=False)
    torch.distributed.init_process_group("gloo", init_method=(args.scratch / "rendezvous").resolve().as_uri(), rank=0, world_size=1)
    parallel_state.initialize_model_parallel()
    torch.manual_seed(117)
    torch.set_num_threads(2)
    rows = []
    try:
        for advantage in (-.75, .75):
            model = CausalModel().to(args.device)
            raw = []
            for turn, (inputs, outputs) in enumerate((
                ([1, 2], [3, 4]), ([1, 2, 3, 4, 5], [6, 7]),
                ([1, 2, 6, 7, 8], [9, 10]),  # Actual eviction changes the prefix.
                ([1, 2, 3, 4, 5, 6, 7, 11], [12, 13]))):
                raw.append(payload(turn, inputs, outputs, advantage, model, args.device))
            packed = coalesce_payloads(tuple(raw))
            assert len(packed) == 2
            original = train_step(deepcopy(model), raw, args.device)
            combined = train_step(deepcopy(model), packed, args.device)
            for left, right in zip(original, combined, strict=True):
                torch.testing.assert_close(left, right, rtol=1e-8, atol=1e-10)
            rows.append({"advantage": advantage, "segments_before": len(raw), "segments_after": len(packed),
                **{name: (left - right).abs().max().item() for name, left, right in zip(
                    ("loss_max_error", "gradient_max_error", "updated_parameter_max_error"), original, combined, strict=True)}})
        result = {"device": args.device, "native_slime_loss_and_reducer": True, "cases": rows}
        (args.scratch / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("BEHAVE_PACKING_RESULT " + json.dumps(result), flush=True)
    finally:
        parallel_state.destroy_model_parallel()
        torch.distributed.destroy_process_group()


if __name__ == "__main__":
    main()
