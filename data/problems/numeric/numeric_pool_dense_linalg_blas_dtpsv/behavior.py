def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v = v & ((1 << b) - 1)
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def q32_32_sub(a, b):
    return to_signed(to_signed(a, 64) - to_signed(b, 64), 64)


def q32_32_mul(a, b):
    return to_signed((to_signed(a, 64) * to_signed(b, 64)) >> 32, 64)


def trunc_div_toward_zero(n, d):
    an = n if n >= 0 else -n
    ad = d if d >= 0 else -d
    q = an // ad
    return q if (n >= 0) == (d >= 0) else -q


def q32_32_div(a, b):
    den = to_signed(b, 64)
    if den == 0:
        return 0
    num = to_signed(a, 64) << 32
    return to_signed(trunc_div_toward_zero(num, den), 64)


class PackedTriangularSolve4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_trans,
        in_diag,
        in_ap0,
        in_ap1,
        in_ap2,
        in_ap3,
        in_ap4,
        in_ap5,
        in_ap6,
        in_ap7,
        in_ap8,
        in_ap9,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
    ):
        if in_uplo:
            a00 = in_ap0
            a01 = in_ap1
            a11 = in_ap2
            a02 = in_ap3
            a12 = in_ap4
            a22 = in_ap5
            a03 = in_ap6
            a13 = in_ap7
            a23 = in_ap8
            a33 = in_ap9
        else:
            a00 = in_ap0
            a10 = in_ap1
            a20 = in_ap2
            a30 = in_ap3
            a11 = in_ap4
            a21 = in_ap5
            a31 = in_ap6
            a22 = in_ap7
            a32 = in_ap8
            a33 = in_ap9

        b0 = in_x0
        b1 = in_x1
        b2 = in_x2
        b3 = in_x3

        if not in_trans:
            if in_uplo:
                x3_pre = b3
                x3 = x3_pre if in_diag else q32_32_div(x3_pre, a33)
                x2_pre = q32_32_sub(b2, q32_32_mul(x3, a23))
                x2 = x2_pre if in_diag else q32_32_div(x2_pre, a22)
                x1_pre = q32_32_sub(q32_32_sub(b1, q32_32_mul(x3, a13)), q32_32_mul(x2, a12))
                x1 = x1_pre if in_diag else q32_32_div(x1_pre, a11)
                x0_pre = q32_32_sub(q32_32_sub(q32_32_sub(b0, q32_32_mul(x3, a03)), q32_32_mul(x2, a02)), q32_32_mul(x1, a01))
                x0 = x0_pre if in_diag else q32_32_div(x0_pre, a00)
            else:
                x0_pre = b0
                x0 = x0_pre if in_diag else q32_32_div(x0_pre, a00)
                x1_pre = q32_32_sub(b1, q32_32_mul(x0, a10))
                x1 = x1_pre if in_diag else q32_32_div(x1_pre, a11)
                x2_pre = q32_32_sub(q32_32_sub(b2, q32_32_mul(x0, a20)), q32_32_mul(x1, a21))
                x2 = x2_pre if in_diag else q32_32_div(x2_pre, a22)
                x3_pre = q32_32_sub(q32_32_sub(q32_32_sub(b3, q32_32_mul(x0, a30)), q32_32_mul(x1, a31)), q32_32_mul(x2, a32))
                x3 = x3_pre if in_diag else q32_32_div(x3_pre, a33)
        else:
            if in_uplo:
                x0_pre = b0
                x0 = x0_pre if in_diag else q32_32_div(x0_pre, a00)
                x1_pre = q32_32_sub(b1, q32_32_mul(a01, x0))
                x1 = x1_pre if in_diag else q32_32_div(x1_pre, a11)
                x2_pre = q32_32_sub(q32_32_sub(b2, q32_32_mul(a02, x0)), q32_32_mul(a12, x1))
                x2 = x2_pre if in_diag else q32_32_div(x2_pre, a22)
                x3_pre = q32_32_sub(q32_32_sub(q32_32_sub(b3, q32_32_mul(a03, x0)), q32_32_mul(a13, x1)), q32_32_mul(a23, x2))
                x3 = x3_pre if in_diag else q32_32_div(x3_pre, a33)
            else:
                x3_pre = b3
                x3 = x3_pre if in_diag else q32_32_div(x3_pre, a33)
                x2_pre = q32_32_sub(b2, q32_32_mul(a32, x3))
                x2 = x2_pre if in_diag else q32_32_div(x2_pre, a22)
                x1_pre = q32_32_sub(q32_32_sub(b1, q32_32_mul(a31, x3)), q32_32_mul(a21, x2))
                x1 = x1_pre if in_diag else q32_32_div(x1_pre, a11)
                x0_pre = q32_32_sub(q32_32_sub(q32_32_sub(b0, q32_32_mul(a30, x3)), q32_32_mul(a20, x2)), q32_32_mul(a10, x1))
                x0 = x0_pre if in_diag else q32_32_div(x0_pre, a00)

        return {
            "out_x0": to_unsigned(x0, 64),
            "out_x1": to_unsigned(x1, 64),
            "out_x2": to_unsigned(x2, 64),
            "out_x3": to_unsigned(x3, 64),
        }
