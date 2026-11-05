from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_sqrt


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class QuaternionNormalizeF32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_n0 = 0
        self.out_n1 = 0
        self.out_n2 = 0
        self.out_n3 = 0

    def process(self, in_q0, in_q1, in_q2, in_q3):
        q0 = in_q0
        q1 = in_q1
        q2 = in_q2
        q3 = in_q3

        s0 = fp32_mul(q0, q0)
        s1 = fp32_mul(q1, q1)
        s2 = fp32_mul(q2, q2)
        s3 = fp32_mul(q3, q3)
        a0 = fp32_add(s0, s1)
        a1 = fp32_add(a0, s2)
        squared_norm = fp32_add(a1, s3)
        norm = fp32_sqrt(squared_norm)
        n0 = fp32_div(q0, norm)
        n1 = fp32_div(q1, norm)
        n2 = fp32_div(q2, norm)
        n3 = fp32_div(q3, norm)

        self.out_n0 = to_unsigned(n0, 32)
        self.out_n1 = to_unsigned(n1, 32)
        self.out_n2 = to_unsigned(n2, 32)
        self.out_n3 = to_unsigned(n3, 32)

        return {
            "out_n0": self.out_n0,
            "out_n1": self.out_n1,
            "out_n2": self.out_n2,
            "out_n3": self.out_n3,
        }