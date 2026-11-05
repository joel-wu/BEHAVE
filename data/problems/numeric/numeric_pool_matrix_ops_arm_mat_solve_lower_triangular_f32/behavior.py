from fp_lib import fp32_mul, fp32_sub, fp32_div


class LowerTriangularSolveF32_4x1:
    def __init__(self):
        self.reset()

    def reset(self):
        self.lt = [0] * 16
        self.a = [0] * 16

    def process(self, in_lt_base, in_a_base):
        lt00 = self.lt[(in_lt_base + 0) & 0xF]
        lt10 = self.lt[(in_lt_base + 4) & 0xF]
        lt11 = self.lt[(in_lt_base + 5) & 0xF]
        lt20 = self.lt[(in_lt_base + 8) & 0xF]
        lt21 = self.lt[(in_lt_base + 9) & 0xF]
        lt22 = self.lt[(in_lt_base + 10) & 0xF]
        lt30 = self.lt[(in_lt_base + 12) & 0xF]
        lt31 = self.lt[(in_lt_base + 13) & 0xF]
        lt32 = self.lt[(in_lt_base + 14) & 0xF]
        lt33 = self.lt[(in_lt_base + 15) & 0xF]

        a0 = self.a[(in_a_base + 0) & 0xF]
        a1 = self.a[(in_a_base + 1) & 0xF]
        a2 = self.a[(in_a_base + 2) & 0xF]
        a3 = self.a[(in_a_base + 3) & 0xF]

        singular = ((lt00 & 0x7FFFFFFF) == 0 or
                    (lt11 & 0x7FFFFFFF) == 0 or
                    (lt22 & 0x7FFFFFFF) == 0 or
                    (lt33 & 0x7FFFFFFF) == 0)

        x0 = fp32_div(a0, lt00)
        t1 = fp32_sub(a1, fp32_mul(lt10, x0))
        x1 = fp32_div(t1, lt11)
        t2a = fp32_sub(a2, fp32_mul(lt20, x0))
        t2 = fp32_sub(t2a, fp32_mul(lt21, x1))
        x2 = fp32_div(t2, lt22)
        t3a = fp32_sub(a3, fp32_mul(lt30, x0))
        t3b = fp32_sub(t3a, fp32_mul(lt31, x1))
        t3 = fp32_sub(t3b, fp32_mul(lt32, x2))
        x3 = fp32_div(t3, lt33)

        keep = 0xFFFFFFFF if not singular else 0x00000000
        return {
            "out_x0": x0 & keep,
            "out_x1": x1 & keep,
            "out_x2": x2 & keep,
            "out_x3": x3 & keep,
            "out_singular": 1 if singular else 0,
        }