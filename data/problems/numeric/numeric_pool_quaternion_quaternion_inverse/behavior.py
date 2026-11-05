from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_neg


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class QuaternionInverseF32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_q0, in_q1, in_q2, in_q3):
        s0 = fp32_mul(in_q0, in_q0)
        s1 = fp32_mul(in_q1, in_q1)
        s2 = fp32_mul(in_q2, in_q2)
        s3 = fp32_mul(in_q3, in_q3)
        sum01 = fp32_add(s0, s1)
        sum012 = fp32_add(sum01, s2)
        norm_sq = fp32_add(sum012, s3)
        c0 = in_q0
        c1 = fp32_neg(in_q1)
        c2 = fp32_neg(in_q2)
        c3 = fp32_neg(in_q3)
        return {
            "out_q0": to_unsigned(fp32_div(c0, norm_sq), 32),
            "out_q1": to_unsigned(fp32_div(c1, norm_sq), 32),
            "out_q2": to_unsigned(fp32_div(c2, norm_sq), 32),
            "out_q3": to_unsigned(fp32_div(c3, norm_sq), 32),
        }