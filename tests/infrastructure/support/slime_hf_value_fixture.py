"""Actual HF readers/value heads on CPU; topology queries alone are stubbed."""

import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from safetensors.torch import save_file
from slime.backends.megatron_utils import hf_to_megatron
from slime.backends.megatron_utils.model_provider import LinearForLastLayer

from behave.train.slime_launch import install_critic_hf_loader


class Model(torch.nn.Module):
    def __init__(self, role, *, output=True):
        super().__init__()
        self.role = role
        self.config = SimpleNamespace(sequence_parallel=False, init_method_std=.02)
        self.embedding = torch.nn.Module()
        self.embedding.word_embeddings = torch.nn.Embedding(8, 4)
        self.decoder = torch.nn.Module()
        self.decoder.final_layernorm = torch.nn.LayerNorm(4, bias=False)
        if output:
            self.output_layer = (LinearForLastLayer(4, 1, config=self.config) if role == "critic"
                                 else torch.nn.Linear(4, 8, bias=False))


def main():
    torch.manual_seed(17)
    root = Path.cwd() / "initial-weights"
    root.mkdir()
    (root / "config.json").write_text(json.dumps({"model_type": "qwen3_5", "text_config": {
        "model_type": "qwen3_5_text", "hidden_size": 4, "intermediate_size": 8,
        "num_hidden_layers": 1, "num_attention_heads": 1, "num_key_value_heads": 1,
        "head_dim": 4, "vocab_size": 5, "tie_word_embeddings": False}}))
    tensors = {"model.language_model.embed_tokens.weight": torch.arange(20.).reshape(5, 4),
               "model.language_model.norm.weight": torch.arange(1., 5.),
               "lm_head.weight": torch.arange(20., 40.).reshape(5, 4)}
    save_file(tensors, root / "model.safetensors")
    args = SimpleNamespace(num_experts=None, padded_vocab_size=8)
    with ExitStack() as stack:
        for name, value in (("get_expert_model_parallel_world_size", 1),
                            ("get_expert_model_parallel_rank", 0)):
            stack.enter_context(patch("megatron.core.mpu." + name, return_value=value))
        stack.enter_context(patch("slime.backends.megatron_utils.update_weight.common.get_transformer_layer_offset",
                                  lambda config: 0))
        critic = Model("critic")
        try:
            hf_to_megatron.load_hf_weights(args, [critic], root)
        except KeyError as error:
            assert "output_layer.bias" in str(error), error
        else:
            raise AssertionError("pinned upstream reproducer no longer fails; re-audit bridge")

        before = {name: tensor.detach().clone() for name, tensor in critic.output_layer.named_parameters()}
        original_loader = hf_to_megatron.load_hf_weights
        install_critic_hf_loader()
        installed_loader = hf_to_megatron.load_hf_weights
        install_critic_hf_loader()
        assert hf_to_megatron.load_hf_weights is installed_loader
        hf_to_megatron.load_hf_weights(args, [critic], root)
        for name, tensor in critic.output_layer.named_parameters():
            torch.testing.assert_close(tensor, before[name], rtol=0, atol=0)
        torch.testing.assert_close(critic.embedding.word_embeddings.weight[:5], tensors["model.language_model.embed_tokens.weight"])
        assert not torch.count_nonzero(critic.embedding.word_embeddings.weight[5:])
        torch.testing.assert_close(critic.decoder.final_layernorm.weight, tensors["model.language_model.norm.weight"])

        # Actor remains byte-for-byte equivalent to the original reader,
        # including vocabulary padding. A headless pipeline stage also loads.
        actor, expected = Model("actor"), Model("actor")
        original_loader(args, [expected], root)
        hf_to_megatron.load_hf_weights(args, [actor], root)
        for name, tensor in actor.state_dict().items():
            torch.testing.assert_close(tensor, expected.state_dict()[name], rtol=0, atol=0)
        hf_to_megatron.load_hf_weights(args, [Model("critic", output=False)], root)
        bad = Model("critic")
        bad.output_layer = torch.nn.Linear(4, 1)
        try:
            hf_to_megatron.load_hf_weights(args, [bad], root)
        except ValueError as error:
            assert "native scalar value head" in str(error)
        else:
            raise AssertionError("unknown output head was silently skipped")

        # No missing/misshapen backbone tensor is excused by the head exclusion.
        for values, error_type in (({k: v for k, v in tensors.items() if "norm" not in k}, KeyError),
                                   ({**tensors, "model.language_model.norm.weight": torch.ones(3)}, ValueError)):
            save_file(values, root / "model.safetensors")
            try:
                hf_to_megatron.load_hf_weights(args, [Model("critic")], root)
            except error_type:
                pass
            else:
                raise AssertionError("invalid backbone checkpoint was accepted")

        # Both the retained value head and the loaded backbone remain trainable.
        optimizer = torch.optim.Adam(critic.parameters(), lr=.01)
        old_backbone = critic.embedding.word_embeddings.weight.detach().clone()
        loss = (critic.output_layer(critic.embedding.word_embeddings(torch.tensor([1])))[0] - 1).square().mean()
        loss.backward()
        assert all(p.grad is not None for p in critic.output_layer.parameters())
        optimizer.step()
        assert not torch.equal(critic.output_layer.weight, before["weight"])
        assert not torch.equal(critic.embedding.word_embeddings.weight, old_backbone)
        # Native/state-dict continuation sees the trained head, not the HF view.
        restored = Model("critic")
        restored.load_state_dict(critic.state_dict())
        torch.testing.assert_close(restored.output_layer.weight, critic.output_layer.weight, rtol=0, atol=0)
    assert not torch.cuda.is_initialized()
    print("BEHAVE_CRITIC_HF_OK")


if __name__ == "__main__":
    main()
