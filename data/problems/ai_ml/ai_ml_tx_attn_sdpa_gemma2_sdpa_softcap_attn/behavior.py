from fp_lib import fp32_add, fp32_div, fp32_mul


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def tanh_fp32(x):
    """Piecewise tanh approximation aligned with the RTL lookup table.

    The hardware implementation uses a fixed piecewise FP32 lookup keyed on
    the absolute-value FP32 bit pattern.  Behavior mirrors that exact table
    so cosim is bit-exact.
    """
    x = to_unsigned(x, 32)
    sign = (x >> 31) & 1
    exp = (x >> 23) & 0xFF
    mant = x & 0x7FFFFF
    absx = x & 0x7FFFFFFF

    if exp == 0xFF and mant != 0:
        return 0x7FC00000
    if x == 0x7F800000:
        return 0x3F800000
    if x == 0xFF800000:
        return 0xBF800000
    if x == 0x00000000 or x == 0x80000000:
        return x
    if exp == 0:
        # Subnormal: signed zero with same sign.
        return (sign << 31)
    if absx < 0x3D800000:
        return x
    if absx < 0x3E800000:
        return 0xBE4C0000 if sign else 0x3E4C0000
    if absx < 0x3F000000:
        return 0xBECC0000 if sign else 0x3ECC0000
    if absx < 0x3F400000:
        return 0xBF180000 if sign else 0x3F180000
    if absx < 0x3F800000:
        return 0xBF400000 if sign else 0x3F400000
    if absx < 0x3FC00000:
        return 0xBF666666 if sign else 0x3F666666
    if absx < 0x40000000:
        return 0xBF733333 if sign else 0x3F733333
    if absx < 0x40400000:
        return 0xBF7A0000 if sign else 0x3F7A0000
    if absx < 0x40800000:
        return 0xBF7D999A if sign else 0x3F7D999A
    if absx < 0x40A00000:
        return 0xBF7F0000 if sign else 0x3F7F0000
    return 0xBF800000 if sign else 0x3F800000


class Gemma2SdpaSoftcapAttn:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def _row(self, q0, q1, q2, q3, k0, k1, k2, k3, mask, mask_valid, scaling, softcap):
        p0 = fp32_mul(q0, k0)
        p1 = fp32_mul(q1, k1)
        p2 = fp32_mul(q2, k2)
        p3 = fp32_mul(q3, k3)
        s0 = fp32_add(p0, p1)
        s1 = fp32_add(p2, p3)
        dot = fp32_add(s0, s1)
        scaled = fp32_mul(dot, scaling)
        divided = fp32_div(scaled, softcap)
        t = tanh_fp32(divided)
        softcapped = fp32_mul(t, softcap)
        if mask_valid == 1:
            out = fp32_add(softcapped, mask)
        else:
            out = softcapped
        return to_unsigned(out, 32)

    def process(
        self,
        in_q0,
        in_q1,
        in_q2,
        in_q3,
        in_k0_0,
        in_k0_1,
        in_k0_2,
        in_k0_3,
        in_k1_0,
        in_k1_1,
        in_k1_2,
        in_k1_3,
        in_k2_0,
        in_k2_1,
        in_k2_2,
        in_k2_3,
        in_k3_0,
        in_k3_1,
        in_k3_2,
        in_k3_3,
        in_mask_valid,
        in_mask0,
        in_mask1,
        in_mask2,
        in_mask3,
        in_scaling,
        in_softcap,
    ):
        return {
            "out_attn0": self._row(in_q0, in_q1, in_q2, in_q3,
                                   in_k0_0, in_k0_1, in_k0_2, in_k0_3,
                                   in_mask0, in_mask_valid, in_scaling, in_softcap),
            "out_attn1": self._row(in_q0, in_q1, in_q2, in_q3,
                                   in_k1_0, in_k1_1, in_k1_2, in_k1_3,
                                   in_mask1, in_mask_valid, in_scaling, in_softcap),
            "out_attn2": self._row(in_q0, in_q1, in_q2, in_q3,
                                   in_k2_0, in_k2_1, in_k2_2, in_k2_3,
                                   in_mask2, in_mask_valid, in_scaling, in_softcap),
            "out_attn3": self._row(in_q0, in_q1, in_q2, in_q3,
                                   in_k3_0, in_k3_1, in_k3_2, in_k3_3,
                                   in_mask3, in_mask_valid, in_scaling, in_softcap),
        }
