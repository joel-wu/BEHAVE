"""Qwen's existing model spec with an autograd-correct CP gather boundary.

Use PyTorch's gather/reduce-scatter pair and cached CPU slicing metadata. This
preserves cross-rank input gradients without an extra full-context all-reduce.
Weights, attention kernels and checkpoint parameter names stay unchanged.
"""

import torch
from megatron.core import mpu, tensor_parallel
from slime_plugins.models.qwen3_5 import Attention as UpstreamAttention
from slime_plugins.models.qwen3_5 import get_qwen3_5_spec as upstream_spec
from torch.distributed.nn.functional import all_gather

from .slime_launch import context_slices


class Attention(UpstreamAttention):
    def forward(self, hidden_states, attention_mask=None, key_value_states=None,
                inference_context=None, rotary_pos_emb=None, rotary_pos_cos=None,
                rotary_pos_sin=None, rotary_pos_cos_sin=None, attention_bias=None,
                packed_seq_params=None, sequence_len_offset=None, *, inference_params=None):
        cp_size = mpu.get_context_parallel_world_size()
        if cp_size == 1:
            return super().forward(hidden_states, attention_mask, key_value_states,
                inference_context, rotary_pos_emb, rotary_pos_cos, rotary_pos_sin,
                rotary_pos_cos_sin, attention_bias, packed_seq_params, sequence_len_offset,
                inference_params=inference_params)

        # The pinned parent performs these same operations with CUDA scalars
        # as Python slice indices. Reuse one CPU plan instead of synchronizing
        # repeatedly in every linear-attention layer and its recomputation.
        plan = context_slices(packed_seq_params, cp_size)
        if self.args.sequence_parallel:
            hidden_states = tensor_parallel.gather_from_sequence_parallel_region(
                hidden_states, tensor_parallel_output_grad=False,
                group=mpu.get_tensor_model_parallel_group())
        # Different CP ranks own different output tokens. Native autograd
        # gather sums their input-gradient contributions with reduce-scatter.
        gathered = all_gather(hidden_states, group=mpu.get_context_parallel_group())
        whole = torch.cat([gathered[rank][part] for rank, part in plan.gather], dim=0)
        output = self.hf_forward(whole.permute(1, 0, 2), packed_seq_params).permute(1, 0, 2)
        output = torch.cat([output[part] for part in plan.scatter[mpu.get_context_parallel_rank()]], dim=0)
        if self.args.sequence_parallel:
            output = tensor_parallel.scatter_to_sequence_parallel_region(
                output, group=mpu.get_tensor_model_parallel_group())
        return output, None


def get_qwen3_5_spec(args, config, vp_stage):
    """Reuse Slime's entire model spec, replacing only its hybrid boundary."""
    if getattr(args, "use_critic", False):
        # Model specs execute in every native worker, before its checkpoint
        # loader. Actor HF loads and all native cold restores remain upstream.
        from .slime_launch import install_critic_hf_loader

        install_critic_hf_loader()
    spec = upstream_spec(args, config, vp_stage)
    for layer in spec.layer_specs:
        attention = layer.submodules.self_attention
        if attention.module is UpstreamAttention:
            attention.module = Attention
    return spec
