def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


NEG_HALF = -16384
SQRT3_BY_2 = 28378


class ControlFocTransformInvClarkeTransform2pTo3p:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_alpha, in_beta):
        alpha = to_signed(in_alpha, 16)
        beta = to_signed(in_beta, 16)

        a_sat = sat16(alpha)

        b_alpha_prod_q30 = alpha * NEG_HALF
        b_beta_prod_q30 = beta * SQRT3_BY_2
        b_alpha_q15 = b_alpha_prod_q30 >> 15
        b_beta_q15 = b_beta_prod_q30 >> 15
        b_sat = sat16(b_alpha_q15 + b_beta_q15)

        c_alpha_prod_q30 = alpha * NEG_HALF
        c_beta_prod_q30 = beta * SQRT3_BY_2
        c_alpha_q15 = c_alpha_prod_q30 >> 15
        c_beta_q15 = c_beta_prod_q30 >> 15
        c_sat = sat16(c_alpha_q15 - c_beta_q15)

        return {
            "out_a": to_unsigned(a_sat, 16),
            "out_b": to_unsigned(b_sat, 16),
            "out_c": to_unsigned(c_sat, 16),
        }