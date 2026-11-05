from fp_lib import fp32_add, fp32_mul, fp32_neg, fp32_cmp


def to_unsigned(v, bits):
    return v & ((1 << bits) - 1)


class CubicSplineInterpolatorF32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_y = 0

    def process(
        self,
        in_xq,
        x0,
        x1,
        x2,
        x3,
        y0,
        y1,
        y2,
        y3,
        b0,
        b1,
        b2,
        c0,
        c1,
        c2,
        d0,
        d1,
        d2,
    ):
        if fp32_cmp(in_xq, x1) in (-1, 0):
            xi, yi, bi, ci, di = x0, y0, b0, c0, d0
        elif fp32_cmp(in_xq, x2) in (-1, 0):
            xi, yi, bi, ci, di = x1, y1, b1, c1, d1
        else:
            xi, yi, bi, ci, di = x2, y2, b2, c2, d2

        dx = fp32_add(in_xq, fp32_neg(xi))
        dx2 = fp32_mul(dx, dx)
        dx3 = fp32_mul(dx2, dx)
        t1 = fp32_mul(bi, dx)
        t2 = fp32_mul(ci, dx2)
        t3 = fp32_mul(di, dx3)
        s1 = fp32_add(yi, t1)
        s2 = fp32_add(s1, t2)
        out_y = fp32_add(s2, t3)

        self.out_y = to_unsigned(out_y, 32)
        return {"out_y": self.out_y}
