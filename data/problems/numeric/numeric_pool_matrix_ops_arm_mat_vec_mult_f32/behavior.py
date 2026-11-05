from fp_lib import fp32_add, fp32_mul


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class MatrixVectorMultiplyF32_4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_y0 = 0
        self.out_y1 = 0
        self.out_y2 = 0
        self.out_y3 = 0

    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_v0, in_v1, in_v2, in_v3
    ):
        p00 = fp32_mul(in_a00, in_v0)
        p01 = fp32_mul(in_a01, in_v1)
        p02 = fp32_mul(in_a02, in_v2)
        p03 = fp32_mul(in_a03, in_v3)
        s00 = fp32_add(p00, p01)
        s01 = fp32_add(s00, p02)
        out_y0 = fp32_add(s01, p03)

        p10 = fp32_mul(in_a10, in_v0)
        p11 = fp32_mul(in_a11, in_v1)
        p12 = fp32_mul(in_a12, in_v2)
        p13 = fp32_mul(in_a13, in_v3)
        s10 = fp32_add(p10, p11)
        s11 = fp32_add(s10, p12)
        out_y1 = fp32_add(s11, p13)

        p20 = fp32_mul(in_a20, in_v0)
        p21 = fp32_mul(in_a21, in_v1)
        p22 = fp32_mul(in_a22, in_v2)
        p23 = fp32_mul(in_a23, in_v3)
        s20 = fp32_add(p20, p21)
        s21 = fp32_add(s20, p22)
        out_y2 = fp32_add(s21, p23)

        p30 = fp32_mul(in_a30, in_v0)
        p31 = fp32_mul(in_a31, in_v1)
        p32 = fp32_mul(in_a32, in_v2)
        p33 = fp32_mul(in_a33, in_v3)
        s30 = fp32_add(p30, p31)
        s31 = fp32_add(s30, p32)
        out_y3 = fp32_add(s31, p33)

        self.out_y0 = to_unsigned(out_y0, 32)
        self.out_y1 = to_unsigned(out_y1, 32)
        self.out_y2 = to_unsigned(out_y2, 32)
        self.out_y3 = to_unsigned(out_y3, 32)

        return {
            "out_y0": self.out_y0,
            "out_y1": self.out_y1,
            "out_y2": self.out_y2,
            "out_y3": self.out_y3,
        }