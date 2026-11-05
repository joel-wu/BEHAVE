def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat_s16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


TWO_THIRDS_Q15 = 21845
NEG_ONE_THIRD_Q15 = -10923
ONE_BY_SQRT3_Q15 = 18919
TWO_BY_SQRT3_Q15 = 37837


class ControlFocTransformClarkeTransform3pTo2p:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_full_clarke, in_ia, in_ib, in_ic):
        ia = to_signed(in_ia, 16)
        ib = to_signed(in_ib, 16)
        ic = to_signed(in_ic, 16)

        if in_full_clarke:
            sum_alpha = ia * TWO_THIRDS_Q15 + ib * NEG_ONE_THIRD_Q15 + ic * NEG_ONE_THIRD_Q15
            alpha = sat_s16(sum_alpha >> 15)
            sum_beta = ib * ONE_BY_SQRT3_Q15 + ic * (-ONE_BY_SQRT3_Q15)
            beta = sat_s16(sum_beta >> 15)
        else:
            alpha = sat_s16(ia)
            sum_beta = ia * ONE_BY_SQRT3_Q15 + ib * TWO_BY_SQRT3_Q15
            beta = sat_s16(sum_beta >> 15)

        return {
            "out_i_alpha": to_unsigned(alpha, 16),
            "out_i_beta": to_unsigned(beta, 16),
        }