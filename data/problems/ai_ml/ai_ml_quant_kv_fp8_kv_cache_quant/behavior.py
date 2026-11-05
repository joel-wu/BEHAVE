from fp_lib import bf16_to_fp32, fp32_cmp, fp32_div, fp32_is_inf, fp32_is_nan


FP32_448 = 0x43E00000
FP32_1E4 = 0x38D1B717


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def fp32_sign(x):
    return (x >> 31) & 1


def fp32_exp(x):
    return (x >> 23) & 0xFF


def fp32_frac(x):
    return x & 0x7FFFFF


def clamp_amax(in_amax_fp32):
    if fp32_is_nan(in_amax_fp32) or fp32_cmp(in_amax_fp32, FP32_1E4) < 0:
        return FP32_1E4
    return in_amax_fp32


def ceil_pow2_positive_fp32(x):
    exp = fp32_exp(x)
    bumped_exp = exp + (1 if fp32_frac(x) != 0 else 0)
    if bumped_exp == 0:
        return 0x00800000
    if bumped_exp >= 0xFF:
        return 0x7F800000
    return to_unsigned(bumped_exp << 23, 32)


def highest_set_bit_23(x):
    pos = 0
    for i in range(23):
        if (x >> i) & 1:
            pos = i
    return pos


def encode_e4m3(y):
    sign = fp32_sign(y)
    exp = fp32_exp(y)
    frac = fp32_frac(y)

    if fp32_is_nan(y):
        return 0x7F
    if fp32_is_inf(y):
        return 0xFE if sign else 0x7E
    if exp == 0 and frac == 0:
        return 0x80 if sign else 0x00

    subnormal_input = 1 if exp == 0 else 0
    leading_one_pos = highest_set_bit_23(frac)
    exponent_unbiased = (leading_one_pos - 149) if subnormal_input else (exp - 127)
    significand24 = ((frac << (23 - leading_one_pos)) if subnormal_input else ((1 << 23) | frac)) & 0xFFFFFF
    target_exp = exponent_unbiased + 7

    if target_exp >= 1:
        if target_exp > 15:
            return 0xFE if sign else 0x7E
        keep = significand24 >> 20
        lost = significand24 & ((1 << 20) - 1)
        half = 1 << 19
        if lost > half or (lost == half and (keep & 1) != 0):
            keep += 1
        if keep == 16:
            keep = 8
            target_exp += 1
            if target_exp > 15:
                return 0xFE if sign else 0x7E
        if target_exp == 15 and keep > 14:
            keep = 14
        return to_unsigned((sign << 7) | (target_exp << 3) | (keep & 0x7), 8)

    total_shift = 14 - exponent_unbiased
    keep = significand24 >> total_shift
    lost = significand24 & ((1 << total_shift) - 1)
    half = 1 << (total_shift - 1)
    if lost > half or (lost == half and (keep & 1) != 0):
        keep += 1
    if keep >= 8:
        return to_unsigned((sign << 7) | (1 << 3), 8)
    if keep == 0:
        return 0x80 if sign else 0x00
    return to_unsigned((sign << 7) | keep, 8)


class AiMlQuantKvFp8KvCacheQuant:
    def __init__(self):
        self.reset()

    def reset(self):
        self.idle = 0

    def process(
        self,
        in_k0_bf16,
        in_k1_bf16,
        in_k2_bf16,
        in_k3_bf16,
        in_amax_fp32,
        in_head_dim,
        in_quant_block_size,
        in_cache_block_size,
        in_slot_mapping,
        in_token_idx,
        in_head_dim_idx,
        in_use_ue8m0,
    ):
        slot_idx = to_signed(in_slot_mapping, 32)
        if slot_idx < 0 or in_quant_block_size == 0 or in_cache_block_size == 0 or in_head_dim_idx >= in_head_dim:
            return {
                "out_q0_fp8": 0,
                "out_q1_fp8": 0,
                "out_q2_fp8": 0,
                "out_q3_fp8": 0,
                "out_scale_fp32": 0,
                "out_dst_offset": 0,
                "out_dst_scale_idx": 0,
                "out_write_enable": 0,
            }

        block_idx = slot_idx // in_cache_block_size
        block_offset = slot_idx % in_cache_block_size
        cache_stride = in_head_dim + (in_head_dim * 4 // in_quant_block_size)
        dst_offset = block_idx * in_cache_block_size * cache_stride + block_offset * in_head_dim + in_head_dim_idx
        dst_scale_idx = (
            block_idx * in_cache_block_size * cache_stride
            + in_cache_block_size * in_head_dim
            + ((block_offset * in_head_dim + in_head_dim_idx) * 4 // in_quant_block_size)
        )

        scale = fp32_div(clamp_amax(in_amax_fp32), FP32_448)
        if in_use_ue8m0:
            scale = ceil_pow2_positive_fp32(scale)

        x0 = bf16_to_fp32(in_k0_bf16)
        x1 = bf16_to_fp32(in_k1_bf16)
        x2 = bf16_to_fp32(in_k2_bf16)
        x3 = bf16_to_fp32(in_k3_bf16)

        return {
            "out_q0_fp8": to_unsigned(encode_e4m3(fp32_div(x0, scale)), 8),
            "out_q1_fp8": to_unsigned(encode_e4m3(fp32_div(x1, scale)), 8),
            "out_q2_fp8": to_unsigned(encode_e4m3(fp32_div(x2, scale)), 8),
            "out_q3_fp8": to_unsigned(encode_e4m3(fp32_div(x3, scale)), 8),
            "out_scale_fp32": to_unsigned(scale, 32),
            "out_dst_offset": to_unsigned(dst_offset, 32),
            "out_dst_scale_idx": to_unsigned(dst_scale_idx, 32),
            "out_write_enable": 1,
        }
