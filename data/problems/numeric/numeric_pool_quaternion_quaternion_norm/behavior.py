from fp_lib import fp32_add, fp32_mul, fp32_sqrt


class QuaternionNormFp32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b, in_c, in_d):
        p0 = fp32_mul(in_a, in_a)
        p1 = fp32_mul(in_b, in_b)
        p2 = fp32_mul(in_c, in_c)
        p3 = fp32_mul(in_d, in_d)
        s0 = fp32_add(p0, p1)
        s1 = fp32_add(s0, p2)
        s2 = fp32_add(s1, p3)
        return {"out_norm": fp32_sqrt(s2)}