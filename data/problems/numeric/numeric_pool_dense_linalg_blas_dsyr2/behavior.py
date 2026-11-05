def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SymmetricRank2Update4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_alpha,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
        in_y0,
        in_y1,
        in_y2,
        in_y3,
        in_a00,
        in_a01,
        in_a02,
        in_a03,
        in_a11,
        in_a12,
        in_a13,
        in_a22,
        in_a23,
        in_a33,
    ):
        alpha = to_signed(in_alpha, 64)
        x0 = to_signed(in_x0, 64)
        x1 = to_signed(in_x1, 64)
        x2 = to_signed(in_x2, 64)
        x3 = to_signed(in_x3, 64)
        y0 = to_signed(in_y0, 64)
        y1 = to_signed(in_y1, 64)
        y2 = to_signed(in_y2, 64)
        y3 = to_signed(in_y3, 64)

        a00 = to_signed(in_a00, 64)
        a01 = to_signed(in_a01, 64)
        a02 = to_signed(in_a02, 64)
        a03 = to_signed(in_a03, 64)
        a11 = to_signed(in_a11, 64)
        a12 = to_signed(in_a12, 64)
        a13 = to_signed(in_a13, 64)
        a22 = to_signed(in_a22, 64)
        a23 = to_signed(in_a23, 64)
        a33 = to_signed(in_a33, 64)

        if alpha == 0:
            return {
                "out_a00": to_unsigned(a00, 64),
                "out_a01": to_unsigned(a01, 64),
                "out_a02": to_unsigned(a02, 64),
                "out_a03": to_unsigned(a03, 64),
                "out_a11": to_unsigned(a11, 64),
                "out_a12": to_unsigned(a12, 64),
                "out_a13": to_unsigned(a13, 64),
                "out_a22": to_unsigned(a22, 64),
                "out_a23": to_unsigned(a23, 64),
                "out_a33": to_unsigned(a33, 64),
            }

        if in_uplo:
            if (x0 != 0) or (y0 != 0):
                temp1 = to_signed(alpha * y0, 64)
                temp2 = to_signed(alpha * x0, 64)
                p1 = to_signed(x0 * temp1, 64)
                p2 = to_signed(y0 * temp2, 64)
                s1 = to_signed(a00 + p1, 64)
                a00 = to_signed(s1 + p2, 64)

            if (x1 != 0) or (y1 != 0):
                temp1 = to_signed(alpha * y1, 64)
                temp2 = to_signed(alpha * x1, 64)
                p1 = to_signed(x0 * temp1, 64)
                p2 = to_signed(y0 * temp2, 64)
                s1 = to_signed(a01 + p1, 64)
                a01 = to_signed(s1 + p2, 64)
                p1 = to_signed(x1 * temp1, 64)
                p2 = to_signed(y1 * temp2, 64)
                s1 = to_signed(a11 + p1, 64)
                a11 = to_signed(s1 + p2, 64)

            if (x2 != 0) or (y2 != 0):
                temp1 = to_signed(alpha * y2, 64)
                temp2 = to_signed(alpha * x2, 64)
                p1 = to_signed(x0 * temp1, 64)
                p2 = to_signed(y0 * temp2, 64)
                s1 = to_signed(a02 + p1, 64)
                a02 = to_signed(s1 + p2, 64)
                p1 = to_signed(x1 * temp1, 64)
                p2 = to_signed(y1 * temp2, 64)
                s1 = to_signed(a12 + p1, 64)
                a12 = to_signed(s1 + p2, 64)
                p1 = to_signed(x2 * temp1, 64)
                p2 = to_signed(y2 * temp2, 64)
                s1 = to_signed(a22 + p1, 64)
                a22 = to_signed(s1 + p2, 64)

            if (x3 != 0) or (y3 != 0):
                temp1 = to_signed(alpha * y3, 64)
                temp2 = to_signed(alpha * x3, 64)
                p1 = to_signed(x0 * temp1, 64)
                p2 = to_signed(y0 * temp2, 64)
                s1 = to_signed(a03 + p1, 64)
                a03 = to_signed(s1 + p2, 64)
                p1 = to_signed(x1 * temp1, 64)
                p2 = to_signed(y1 * temp2, 64)
                s1 = to_signed(a13 + p1, 64)
                a13 = to_signed(s1 + p2, 64)
                p1 = to_signed(x2 * temp1, 64)
                p2 = to_signed(y2 * temp2, 64)
                s1 = to_signed(a23 + p1, 64)
                a23 = to_signed(s1 + p2, 64)
                p1 = to_signed(x3 * temp1, 64)
                p2 = to_signed(y3 * temp2, 64)
                s1 = to_signed(a33 + p1, 64)
                a33 = to_signed(s1 + p2, 64)
        else:
            if (x0 != 0) or (y0 != 0):
                temp1 = to_signed(alpha * y0, 64)
                temp2 = to_signed(alpha * x0, 64)
                p1 = to_signed(x0 * temp1, 64)
                p2 = to_signed(y0 * temp2, 64)
                s1 = to_signed(a00 + p1, 64)
                a00 = to_signed(s1 + p2, 64)
                p1 = to_signed(x1 * temp1, 64)
                p2 = to_signed(y1 * temp2, 64)
                s1 = to_signed(a01 + p1, 64)
                a01 = to_signed(s1 + p2, 64)
                p1 = to_signed(x2 * temp1, 64)
                p2 = to_signed(y2 * temp2, 64)
                s1 = to_signed(a02 + p1, 64)
                a02 = to_signed(s1 + p2, 64)
                p1 = to_signed(x3 * temp1, 64)
                p2 = to_signed(y3 * temp2, 64)
                s1 = to_signed(a03 + p1, 64)
                a03 = to_signed(s1 + p2, 64)

            if (x1 != 0) or (y1 != 0):
                temp1 = to_signed(alpha * y1, 64)
                temp2 = to_signed(alpha * x1, 64)
                p1 = to_signed(x1 * temp1, 64)
                p2 = to_signed(y1 * temp2, 64)
                s1 = to_signed(a11 + p1, 64)
                a11 = to_signed(s1 + p2, 64)
                p1 = to_signed(x2 * temp1, 64)
                p2 = to_signed(y2 * temp2, 64)
                s1 = to_signed(a12 + p1, 64)
                a12 = to_signed(s1 + p2, 64)
                p1 = to_signed(x3 * temp1, 64)
                p2 = to_signed(y3 * temp2, 64)
                s1 = to_signed(a13 + p1, 64)
                a13 = to_signed(s1 + p2, 64)

            if (x2 != 0) or (y2 != 0):
                temp1 = to_signed(alpha * y2, 64)
                temp2 = to_signed(alpha * x2, 64)
                p1 = to_signed(x2 * temp1, 64)
                p2 = to_signed(y2 * temp2, 64)
                s1 = to_signed(a22 + p1, 64)
                a22 = to_signed(s1 + p2, 64)
                p1 = to_signed(x3 * temp1, 64)
                p2 = to_signed(y3 * temp2, 64)
                s1 = to_signed(a23 + p1, 64)
                a23 = to_signed(s1 + p2, 64)

            if (x3 != 0) or (y3 != 0):
                temp1 = to_signed(alpha * y3, 64)
                temp2 = to_signed(alpha * x3, 64)
                p1 = to_signed(x3 * temp1, 64)
                p2 = to_signed(y3 * temp2, 64)
                s1 = to_signed(a33 + p1, 64)
                a33 = to_signed(s1 + p2, 64)

        return {
            "out_a00": to_unsigned(a00, 64),
            "out_a01": to_unsigned(a01, 64),
            "out_a02": to_unsigned(a02, 64),
            "out_a03": to_unsigned(a03, 64),
            "out_a11": to_unsigned(a11, 64),
            "out_a12": to_unsigned(a12, 64),
            "out_a13": to_unsigned(a13, 64),
            "out_a22": to_unsigned(a22, 64),
            "out_a23": to_unsigned(a23, 64),
            "out_a33": to_unsigned(a33, 64),
        }