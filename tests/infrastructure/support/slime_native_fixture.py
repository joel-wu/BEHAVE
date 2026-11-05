"""Actual Slime losses and Qwen hybrid TP collectives; not a 27B training run.

Run by an owned torchrun process on four allocated GPUs. No downloads, fake
Megatron modules, replaced attention kernels, Ray cluster or shared mutation.
"""

import argparse
import copy
import hashlib
import inspect
import json
import os
import sys
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("CUDA_HOME", sys.base_prefix)
os.environ.setdefault("MAX_JOBS", "2")

import torch
import torch.distributed as dist
from megatron.core import mpu
from megatron.core.packed_seq_params import PackedSeqParams
from megatron.core.transformer.transformer_config import TransformerConfig
from slime.backends.megatron_utils.cp_utils import get_sum_of_sample_mean
from slime.backends.megatron_utils.loss import compute_advantages_and_returns, value_loss_function
from slime.utils.ppo_utils import compute_cispo_loss, compute_gspo_kl, compute_policy_loss
from slime_plugins.models.qwen3_5 import Attention as UpstreamAttention

from behave.train.slime_launch import ALGORITHMS, preset
from behave.train.slime_qwen import Attention, get_qwen3_5_spec
from behave.train.slime_rollout import SlimeBatch, post_process_rewards


def algorithms():
    """Exercise all upstream estimators, then actual clipped losses/backward."""
    passed = []
    for algorithm in ALGORITHMS:
        from slime.utils.arguments import get_slime_extra_args_provider

        recipe = preset(algorithm)
        parser = get_slime_extra_args_provider()(argparse.ArgumentParser())
        parsed, unknown = parser.parse_known_args((*recipe.arguments(), "--rollout-batch-size", "1"))
        # Router options are parsed by SGLang, not Megatron/Slime's parser.
        assert unknown == ["--router-policy", "consistent_hashing"], unknown
        recipe.validate_args(parsed)
        parsed.global_batch_size = 1
        batch = SlimeBatch(({
            "rollout_id": 4, "index": 0, "session_id": "candidate", "tokens": [1, 2], "response_length": 1,
            "reward": 1., "metadata": {"result": {"id": "fixture"},
                "behave_credit": {"recipe": recipe.as_dict(), "value": 1.}},
        },), ({"reward": 1., "excluded_turns": 0},), recipe)
        output = batch.rollout_output()
        assert output.samples[0][0].session_id == "candidate"
        assert output.metrics["behave/candidate_reward_mean"] == 1.
        assert post_process_rewards(parsed, output.samples[0]) == ([1.], [1.])
        data = {"rollout_log_probs": [torch.tensor([-1., -2.], device="cuda")],
                "rewards": [1.], "values": [torch.tensor([.2, .7], device="cuda")],
                "response_lengths": [2], "total_lengths": [4], "loss_masks": [torch.ones(2, device="cuda")]}
        args = SimpleNamespace(use_rollout_logprobs=True, kl_coef=0., custom_advantage_function_path=None,
                               advantage_estimator=algorithm, gamma=1., lambd=1., use_opd=False,
                               normalize_advantages=recipe.normalize_advantages, kl_loss_type="low_var_kl")
        compute_advantages_and_returns(args, data)
        expected = torch.tensor([.8, .3] if algorithm == "ppo" else [1., 1.], device="cuda")
        if recipe.normalize_advantages:
            expected.zero_()  # Equal-return tokens have zero centered advantage.
        torch.testing.assert_close(data["advantages"][0], expected)
        torch.testing.assert_close(data["returns"][0], torch.ones(2, device="cuda"))
        passed.append(algorithm)

    # Siblings in separate microbatches must sum to one rollout mean.
    denominators = [torch.tensor(4., device="cuda"), torch.tensor(4., device="cuda")]
    masks = [torch.ones(2, device="cuda"), torch.ones(2, device="cuda")]
    together = get_sum_of_sample_mean([4, 4], [2, 2], masks, denominators)(torch.ones(4, device="cuda"))
    separate = sum(get_sum_of_sample_mean([4], [2], [m], [d])(torch.ones(2, device="cuda"))
                   for m, d in zip(masks, denominators, strict=True))
    assert together.item() == separate.item() == 1.

    # Native PPO clip formula, with both advantage signs and clipping arms.
    log_ratio = torch.tensor([-.7, -.1, .1, .7], device="cuda", requires_grad=True)
    advantage = torch.tensor([-1., -1., 1., 1.], device="cuda")
    loss, clipped = compute_policy_loss(-log_ratio, advantage, .2, .2)
    ratio = log_ratio.exp()
    expected_loss = torch.maximum(-ratio * advantage, -ratio.clamp(.8, 1.2) * advantage)
    torch.testing.assert_close(loss, expected_loss)
    torch.testing.assert_close(clipped, torch.tensor([1., 0., 0., 1.], device="cuda"))
    loss.mean().backward()
    torch.testing.assert_close(log_ratio.grad, torch.stack((ratio[0] * 0, ratio[1] / 4,
                                                         -ratio[2] / 4, ratio[3] * 0)))

    log_probs = torch.tensor([-1., -2., -3., -4.], device="cuda", requires_grad=True)
    cispo_log_ratio = torch.tensor([-2., -.1, .1, 2.], device="cuda", requires_grad=True)
    recipe = preset("cispo")
    loss, clipped = compute_cispo_loss(-cispo_log_ratio, log_probs, advantage, recipe.clip_low, recipe.clip_high)
    loss.sum().backward()
    torch.testing.assert_close(log_probs.grad, -cispo_log_ratio.detach().exp().clamp(0., 5.) * advantage)
    torch.testing.assert_close(clipped, torch.tensor([0., 0., 0., 1.], device="cuda"))
    assert cispo_log_ratio.grad is None  # Importance weights are stop-gradient.

    current = torch.tensor([-1., -2., -3.], device="cuda", requires_grad=True)
    old = current.detach() + torch.tensor([.2, 99., .4], device="cuda")
    mask = torch.tensor([1., 0., 1.], device="cuda")
    kl = compute_gspo_kl([current], [old], [current], [mask])
    torch.testing.assert_close(kl, torch.full_like(current, .3))
    kl.sum().backward()
    torch.testing.assert_close(current.grad, torch.tensor([-1.5, 0., -1.5], device="cuda"))

    # Actual upstream critic objective must also be differentiable and update.
    logits = torch.nn.Parameter(torch.zeros(1, 4, 1, device="cuda"))
    batch = {"values": [torch.zeros(2, device="cuda")], "returns": [torch.ones(2, device="cuda")],
             "unconcat_tokens": [torch.tensor([1, 2, 3, 4], device="cuda")],
             "total_lengths": [4], "response_lengths": [2]}
    args = SimpleNamespace(value_clip=.2, rollout_temperature=1., allgather_cp=False)
    loss, _ = value_loss_function(args, batch, logits, lambda x: x.mean())
    torch.testing.assert_close(loss, torch.tensor(1., device="cuda"))
    loss.backward()
    optimizer = torch.optim.SGD([logits], lr=.1)
    optimizer.step()
    assert logits.detach().sum() > 0
    return passed


def weight_roundtrip(module):
    """Use both native converters, including gated QKV's head/group ordering."""
    from slime.backends.megatron_utils.hf_to_megatron.qwen3_5 import qwen3_5_hf_tensor
    from slime.backends.megatron_utils.megatron_to_hf import convert_to_hf

    args = SimpleNamespace(hidden_size=32, num_attention_heads=24, num_query_groups=4,
                           kv_channels=8, vocab_size=48, q_lora_rank=None)
    hf_config = SimpleNamespace(hidden_size=32, num_attention_heads=24, num_key_value_heads=4,
                                head_dim=8, tie_word_embeddings=False)
    tensors = {"decoder.layers.0.self_attention." + name: p.detach()
               for name, p in module.named_parameters()}
    shapes = {"embedding.word_embeddings.weight": (48, 32), "output_layer.weight": (48, 32),
              "decoder.final_layernorm.weight": (32,),
              "decoder.layers.3.self_attention.linear_qkv.weight": (448, 32),
              "decoder.layers.3.self_attention.linear_proj.weight": (32, 192),
              "decoder.layers.3.self_attention.q_layernorm.weight": (8,),
              "decoder.layers.3.self_attention.k_layernorm.weight": (8,),
              "decoder.layers.3.self_attention.linear_qkv.layer_norm_weight": (32,),
              "decoder.layers.3.mlp.linear_fc1.weight": (128, 32),
              "decoder.layers.3.mlp.linear_fc2.weight": (32, 64),
              "decoder.layers.3.mlp.linear_fc1.layer_norm_weight": (32,)}
    tensors.update({name: torch.randn(shape, device="cuda") for name, shape in shapes.items()})
    for name, original in tensors.items():
        exported = dict(convert_to_hf(args, "Qwen3_5Config", "module.module." + name, original))
        reader = SimpleNamespace(get_tensor=exported.__getitem__)
        restored = qwen3_5_hf_tensor(name, reader, hf_config)
        torch.testing.assert_close(restored, original, rtol=0, atol=0, msg=name)
    return len(tensors)


def qwen_tp(root: Path, *, cp: int, tp: int | None = None):
    """Compare the selected TP/CP layout with an unpartitioned module per DP rank."""
    rank, world = dist.get_rank(), dist.get_world_size()
    tp = world // cp if tp is None else tp
    mpu.initialize_model_parallel(tensor_model_parallel_size=tp, context_parallel_size=cp)
    config_path = root / f"model-{cp}"
    if rank == 0:
        config_path.mkdir()
        (config_path / "config.json").write_text(json.dumps({
            "model_type": "qwen3_5_text", "hidden_size": 128, "intermediate_size": 256,
            "num_attention_heads": 4, "num_key_value_heads": 4, "head_dim": 32,
            "num_hidden_layers": 1, "layer_types": ["linear_attention"],
            "linear_num_key_heads": 4, "linear_num_value_heads": 12,
            "linear_key_head_dim": 64, "linear_value_head_dim": 64,
            "linear_conv_kernel_dim": 4, "hidden_act": "silu", "rms_norm_eps": 1e-6,
            "dtype": "bfloat16", "torch_dtype": "bfloat16",
        }))
    dist.barrier()
    torch.manual_seed(291)
    torch.cuda.manual_seed_all(291)
    config = TransformerConfig(num_layers=1, hidden_size=128, num_attention_heads=4,
                               tensor_model_parallel_size=tp, context_parallel_size=cp,
                               sequence_parallel=True, params_dtype=torch.bfloat16)
    args = SimpleNamespace(hf_checkpoint=str(config_path), sequence_parallel=True,
                           qwen_gdn_backend="fla", num_experts=None)
    spec = get_qwen3_5_spec(args, config, vp_stage=None)
    assert spec.layer_specs[0].submodules.self_attention.module is Attention
    module = Attention(args, config, layer_number=1).to(device="cuda", dtype=torch.bfloat16)
    reference = UpstreamAttention(args, config, layer_number=1).to(device="cuda", dtype=torch.bfloat16)
    reference.load_state_dict(module.state_dict())
    original = torch.randn(128, 1, 128, device="cuda", dtype=torch.bfloat16)
    target = torch.randn_like(original)
    boundaries = [0, 48, 128]
    cu = torch.tensor(boundaries, device="cuda", dtype=torch.int32)
    packed = PackedSeqParams(qkv_format="thd", cu_seqlens_q=cu, cu_seqlens_kv=cu,
                             max_seqlen_q=80, max_seqlen_kv=80)
    reference_input = original.detach().clone().requires_grad_()
    full = reference.hf_forward(reference_input.transpose(0, 1), packed).transpose(0, 1)
    (full.float() * target.float()).sum().backward()

    # Match Megatron's zigzag CP partition, followed by sequence-parallel TP.
    indices = torch.arange(128, device="cuda")
    cp_rank, tp_rank = mpu.get_context_parallel_rank(), mpu.get_tensor_model_parallel_rank()
    if cp > 1:
        segments = []
        for begin, end in pairwise(boundaries):
            chunks = indices[begin:end].chunk(2 * cp)
            segments.extend((chunks[cp_rank], chunks[2 * cp - cp_rank - 1]))
        indices = torch.cat(segments)
    indices = indices.chunk(tp)[tp_rank]
    local = original[indices].detach().clone().requires_grad_()
    output, bias = module(local, None, packed_seq_params=packed)
    assert bias is None
    torch.testing.assert_close(output, full.detach()[indices], rtol=.02, atol=.01)
    (output.float() * target[indices].float()).sum().backward()
    torch.testing.assert_close(local.grad, reference_input.grad[indices], rtol=.03, atol=.03)
    relative_error = float((local.grad.float() - reference_input.grad[indices].float()).norm()
                           / reference_input.grad[indices].float().norm().clamp_min(1e-12))
    assert relative_error < .03, relative_error

    if cp > 1:
        # Preserve the original bug as a negative control on this same graph.
        unfixed = copy.deepcopy(reference)
        unfixed.zero_grad(set_to_none=True)
        bad_input = original[indices].detach().clone().requires_grad_()
        bad_output, _ = unfixed(bad_input, None, packed_seq_params=packed)
        (bad_output.float() * target[indices].float()).sum().backward()
        bad_error = float((bad_input.grad.float() - reference_input.grad[indices].float()).norm()
                          / reference_input.grad[indices].float().norm().clamp_min(1e-12))
        # TP4/CP2 can assign an early, causally unaffected slice to a rank.
        # The intentionally broken collective must fail somewhere globally,
        # not necessarily on every local input slice.
        bad_error_max = torch.tensor(bad_error, device="cuda")
        dist.all_reduce(bad_error_max, op=dist.ReduceOp.MAX)
        assert bad_error_max.item() > .05, "upstream changed: re-audit the CP compatibility adapter"

    # CP duplicates the module but each rank owns only a subset of output
    # tokens: its parameter gradients sum across CP, never across replicated TP.
    for (name, param), (other_name, expected) in zip(module.named_parameters(), reference.named_parameters(), strict=True):
        assert name == other_name and param.grad is not None and expected.grad is not None
        if cp > 1:
            dist.all_reduce(param.grad, group=mpu.get_context_parallel_group())
        torch.testing.assert_close(param.grad, expected.grad, rtol=.05, atol=.10, msg=name)
    optimizer = torch.optim.SGD(module.parameters(), lr=1e-3)
    expected_optimizer = torch.optim.SGD(reference.parameters(), lr=1e-3)
    optimizer.step()
    expected_optimizer.step()
    for actual, expected in zip(module.parameters(), reference.parameters(), strict=True):
        torch.testing.assert_close(actual, expected, rtol=.03, atol=.002)
    error = float((local.grad.float() - reference_input.grad[indices].float()).abs().max())
    converted = weight_roundtrip(module)
    mpu.destroy_model_parallel()
    return {"tp": tp, "cp": cp, "forward": True, "input_gradient": True,
            "parameter_gradient": True, "sgd_update": True, "max_input_gradient_error": error,
            "relative_input_gradient_error": relative_error, "weight_conversion_roundtrips": converted}


def main():
    assert int(os.environ["WORLD_SIZE"]) == 4
    torch.set_num_threads(1)
    torch.cuda.set_device(int(os.environ["LOCAL_RANK"]))
    dist.init_process_group("nccl", device_id=torch.device("cuda", int(os.environ["LOCAL_RANK"])))
    root = Path(sys.argv[1])
    mpu.initialize_model_parallel(tensor_model_parallel_size=4)
    losses = algorithms()
    if dist.get_rank() == 0:
        print("BEHAVE_SLIME_ALGORITHMS " + json.dumps(losses), flush=True)
    mpu.destroy_model_parallel()
    records = []
    for cp in (1, 2):
        records.append(qwen_tp(root, cp=cp))
        if dist.get_rank() == 0:
            print("BEHAVE_SLIME_PHASE " + json.dumps(records[-1]), flush=True)
    import sglang.srt.models.qwen3_5 as serving
    import slime_plugins.models.hf_attention as wrapper

    # Read actual runtime source, not the unrelated standalone SGLang version.
    code = inspect.getsource(serving)
    assert code.count("is_last_layer=(layer_id == config.num_hidden_layers - 1)") >= 2
    paths = [Path(inspect.getfile(module)) for module in (serving, wrapper)]
    if dist.get_rank() == 0:
        print("BEHAVE_SLIME_RESULT " + json.dumps({"torch": torch.__version__, "world_size": 4,
              "algorithms": losses, "qwen": records,
              "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}), flush=True)
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
