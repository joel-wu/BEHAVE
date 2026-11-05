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


def descale(x, n):
    if n == 0:
        return x
    return (x + (1 << (n - 1))) >> n


class CompressionJpegTransformJpegIdct1d8pointInt:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_coef0, in_coef1, in_coef2, in_coef3,
        in_coef4, in_coef5, in_coef6, in_coef7,
        in_quant0, in_quant1, in_quant2, in_quant3,
        in_quant4, in_quant5, in_quant6, in_quant7
    ):
        coef0 = to_signed(in_coef0, 16)
        coef1 = to_signed(in_coef1, 16)
        coef2 = to_signed(in_coef2, 16)
        coef3 = to_signed(in_coef3, 16)
        coef4 = to_signed(in_coef4, 16)
        coef5 = to_signed(in_coef5, 16)
        coef6 = to_signed(in_coef6, 16)
        coef7 = to_signed(in_coef7, 16)

        quant0 = to_unsigned(in_quant0, 16)
        quant1 = to_unsigned(in_quant1, 16)
        quant2 = to_unsigned(in_quant2, 16)
        quant3 = to_unsigned(in_quant3, 16)
        quant4 = to_unsigned(in_quant4, 16)
        quant5 = to_unsigned(in_quant5, 16)
        quant6 = to_unsigned(in_quant6, 16)
        quant7 = to_unsigned(in_quant7, 16)

        dq0 = coef0 * quant0
        dq1 = coef1 * quant1
        dq2 = coef2 * quant2
        dq3 = coef3 * quant3
        dq4 = coef4 * quant4
        dq5 = coef5 * quant5
        dq6 = coef6 * quant6
        dq7 = coef7 * quant7

        if coef1 == 0 and coef2 == 0 and coef3 == 0 and coef4 == 0 and coef5 == 0 and coef6 == 0 and coef7 == 0:
            dcval = dq0 << PASS1_BITS
            s0 = dcval
            s1 = dcval
            s2 = dcval
            s3 = dcval
            s4 = dcval
            s5 = dcval
            s6 = dcval
            s7 = dcval
        else:
            z2 = dq2
            z3 = dq6

            z1 = (z2 + z3) * FIX_0_541196100
            tmp2 = z1 + z3 * (-FIX_1_847759065)
            tmp3 = z1 + z2 * FIX_0_765366865

            z2 = dq0
            z3 = dq4

            tmp0 = (z2 + z3) << CONST_BITS
            tmp1 = (z2 - z3) << CONST_BITS

            tmp10 = tmp0 + tmp3
            tmp13 = tmp0 - tmp3
            tmp11 = tmp1 + tmp2
            tmp12 = tmp1 - tmp2

            tmp0 = dq7
            tmp1 = dq5
            tmp2 = dq3
            tmp3 = dq1

            z1 = tmp0 + tmp3
            z2 = tmp1 + tmp2
            z3 = tmp0 + tmp2
            z4 = tmp1 + tmp3
            z5 = (z3 + z4) * FIX_1_175875602

            tmp0 = tmp0 * FIX_0_298631336
            tmp1 = tmp1 * FIX_2_053119869
            tmp2 = tmp2 * FIX_3_072711026
            tmp3 = tmp3 * FIX_1_501321110
            z1 = z1 * (-FIX_0_899976223)
            z2 = z2 * (-FIX_2_562915447)
            z3 = z3 * (-FIX_1_961570560)
            z4 = z4 * (-FIX_0_390180644)

            z3 = z3 + z5
            z4 = z4 + z5

            tmp0 = tmp0 + z1 + z3
            tmp1 = tmp1 + z2 + z4
            tmp2 = tmp2 + z2 + z3
            tmp3 = tmp3 + z1 + z4

            s0 = descale(tmp10 + tmp3, CONST_BITS - PASS1_BITS)
            s7 = descale(tmp10 - tmp3, CONST_BITS - PASS1_BITS)
            s1 = descale(tmp11 + tmp2, CONST_BITS - PASS1_BITS)
            s6 = descale(tmp11 - tmp2, CONST_BITS - PASS1_BITS)
            s2 = descale(tmp12 + tmp1, CONST_BITS - PASS1_BITS)
            s5 = descale(tmp12 - tmp1, CONST_BITS - PASS1_BITS)
            s3 = descale(tmp13 + tmp0, CONST_BITS - PASS1_BITS)
            s4 = descale(tmp13 - tmp0, CONST_BITS - PASS1_BITS)

        return {
            "out_s0": to_unsigned(s0, 16),
            "out_s1": to_unsigned(s1, 16),
            "out_s2": to_unsigned(s2, 16),
            "out_s3": to_unsigned(s3, 16),
            "out_s4": to_unsigned(s4, 16),
            "out_s5": to_unsigned(s5, 16),
            "out_s6": to_unsigned(s6, 16),
            "out_s7": to_unsigned(s7, 16),
        }