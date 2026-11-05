def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


CONST_BITS = 13
PASS1_BITS = 2

FIX_0_298631336 = 2446
FIX_0_390180644 = 3196
FIX_0_541196100 = 4433
FIX_0_765366865 = 6270
FIX_0_899976223 = 7373
FIX_1_175875602 = 9633
FIX_1_501321110 = 12299
FIX_1_847759065 = 15137
FIX_1_961570560 = 16069
FIX_2_053119869 = 16819
FIX_2_562915447 = 20995
FIX_3_072711026 = 25172


class CompressionJpegTransformJpegFdct1d8pointInt:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_s0, in_s1, in_s2, in_s3, in_s4, in_s5, in_s6, in_s7):
        s0 = to_signed(in_s0, 16)
        s1 = to_signed(in_s1, 16)
        s2 = to_signed(in_s2, 16)
        s3 = to_signed(in_s3, 16)
        s4 = to_signed(in_s4, 16)
        s5 = to_signed(in_s5, 16)
        s6 = to_signed(in_s6, 16)
        s7 = to_signed(in_s7, 16)

        tmp0 = s0 + s7
        tmp7 = s0 - s7
        tmp1 = s1 + s6
        tmp6 = s1 - s6
        tmp2 = s2 + s5
        tmp5 = s2 - s5
        tmp3 = s3 + s4
        tmp4 = s3 - s4

        tmp10 = tmp0 + tmp3
        tmp13 = tmp0 - tmp3
        tmp11 = tmp1 + tmp2
        tmp12 = tmp1 - tmp2

        y0 = (tmp10 + tmp11) << PASS1_BITS
        y4 = (tmp10 - tmp11) << PASS1_BITS

        z1e = (tmp12 + tmp13) * FIX_0_541196100
        y2 = (z1e + tmp13 * FIX_0_765366865 + (1 << (CONST_BITS - PASS1_BITS - 1))) >> (CONST_BITS - PASS1_BITS)
        y6 = (z1e + tmp12 * (-FIX_1_847759065) + (1 << (CONST_BITS - PASS1_BITS - 1))) >> (CONST_BITS - PASS1_BITS)

        z1 = tmp4 + tmp7
        z2 = tmp5 + tmp6
        z3 = tmp4 + tmp6
        z4 = tmp5 + tmp7
        z5 = (z3 + z4) * FIX_1_175875602

        tmp4m = tmp4 * FIX_0_298631336
        tmp5m = tmp5 * FIX_2_053119869
        tmp6m = tmp6 * FIX_3_072711026
        tmp7m = tmp7 * FIX_1_501321110
        z1m = z1 * (-FIX_0_899976223)
        z2m = z2 * (-FIX_2_562915447)
        z3m = z3 * (-FIX_1_961570560) + z5
        z4m = z4 * (-FIX_0_390180644) + z5

        y7 = (tmp4m + z1m + z3m + (1 << (CONST_BITS - PASS1_BITS - 1))) >> (CONST_BITS - PASS1_BITS)
        y5 = (tmp5m + z2m + z4m + (1 << (CONST_BITS - PASS1_BITS - 1))) >> (CONST_BITS - PASS1_BITS)
        y3 = (tmp6m + z2m + z3m + (1 << (CONST_BITS - PASS1_BITS - 1))) >> (CONST_BITS - PASS1_BITS)
        y1 = (tmp7m + z1m + z4m + (1 << (CONST_BITS - PASS1_BITS - 1))) >> (CONST_BITS - PASS1_BITS)

        return {
            "out_y0": to_unsigned(y0, 16),
            "out_y1": to_unsigned(y1, 16),
            "out_y2": to_unsigned(y2, 16),
            "out_y3": to_unsigned(y3, 16),
            "out_y4": to_unsigned(y4, 16),
            "out_y5": to_unsigned(y5, 16),
            "out_y6": to_unsigned(y6, 16),
            "out_y7": to_unsigned(y7, 16),
        }