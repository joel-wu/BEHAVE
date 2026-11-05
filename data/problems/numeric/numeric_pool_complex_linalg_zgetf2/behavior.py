def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_zero_div(n, d):
    if d == 0:
        return 0
    q = abs(n) // abs(d)
    return -q if (n < 0) ^ (d < 0) else q


def wrap32(v):
    return to_signed(v, 32)


def c_zero(z):
    return z[0] == 0 and z[1] == 0


def c_abs1(z):
    xr = z[0]
    xi = z[1]
    ar = xr if xr >= 0 else -xr
    ai = xi if xi >= 0 else -xi
    return ar + ai


def c_mul_q16(a, b):
    xr, xi = a
    yr, yi = b
    p_rr = xr * yr
    p_ii = xi * yi
    p_ri = xr * yi
    p_ir = xi * yr
    return (
        wrap32(trunc_zero_div(p_rr - p_ii, 1 << 16)),
        wrap32(trunc_zero_div(p_ri + p_ir, 1 << 16)),
    )


def c_div_q16(a, b):
    xr, xi = a
    yr, yi = b
    den = yr * yr + yi * yi
    if den == 0:
        return (0, 0)
    num_re = xr * yr + xi * yi
    num_im = xi * yr - xr * yi
    return (
        wrap32(trunc_zero_div(num_re * (1 << 16), den)),
        wrap32(trunc_zero_div(num_im * (1 << 16), den)),
    )


def c_sub_wrap(a, b):
    return (wrap32(a[0] - b[0]), wrap32(a[1] - b[1]))


class ComplexLuPivotStep4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a00_re, in_a00_im, in_a01_re, in_a01_im, in_a02_re, in_a02_im, in_a03_re, in_a03_im,
        in_a10_re, in_a10_im, in_a11_re, in_a11_im, in_a12_re, in_a12_im, in_a13_re, in_a13_im,
        in_a20_re, in_a20_im, in_a21_re, in_a21_im, in_a22_re, in_a22_im, in_a23_re, in_a23_im,
        in_a30_re, in_a30_im, in_a31_re, in_a31_im, in_a32_re, in_a32_im, in_a33_re, in_a33_im
    ):
        a = [
            [
                (to_signed(in_a00_re, 32), to_signed(in_a00_im, 32)),
                (to_signed(in_a01_re, 32), to_signed(in_a01_im, 32)),
                (to_signed(in_a02_re, 32), to_signed(in_a02_im, 32)),
                (to_signed(in_a03_re, 32), to_signed(in_a03_im, 32)),
            ],
            [
                (to_signed(in_a10_re, 32), to_signed(in_a10_im, 32)),
                (to_signed(in_a11_re, 32), to_signed(in_a11_im, 32)),
                (to_signed(in_a12_re, 32), to_signed(in_a12_im, 32)),
                (to_signed(in_a13_re, 32), to_signed(in_a13_im, 32)),
            ],
            [
                (to_signed(in_a20_re, 32), to_signed(in_a20_im, 32)),
                (to_signed(in_a21_re, 32), to_signed(in_a21_im, 32)),
                (to_signed(in_a22_re, 32), to_signed(in_a22_im, 32)),
                (to_signed(in_a23_re, 32), to_signed(in_a23_im, 32)),
            ],
            [
                (to_signed(in_a30_re, 32), to_signed(in_a30_im, 32)),
                (to_signed(in_a31_re, 32), to_signed(in_a31_im, 32)),
                (to_signed(in_a32_re, 32), to_signed(in_a32_im, 32)),
                (to_signed(in_a33_re, 32), to_signed(in_a33_im, 32)),
            ],
        ]

        ipiv = [0, 0, 0, 0]
        info = 0

        for j in range(4):
            jp = j
            best = c_abs1(a[j][j])
            for r in range(j + 1, 4):
                mag = c_abs1(a[r][j])
                if mag > best:
                    best = mag
                    jp = r
            ipiv[j] = jp

            if not c_zero(a[jp][j]):
                if jp != j:
                    a[j], a[jp] = a[jp], a[j]
                for r in range(j + 1, 4):
                    a[r][j] = c_div_q16(a[r][j], a[j][j])
            elif info == 0:
                info = j + 1

            if j < 3:
                for r in range(j + 1, 4):
                    for c in range(j + 1, 4):
                        a[r][c] = c_sub_wrap(a[r][c], c_mul_q16(a[r][j], a[j][c]))

        return {
            "out_a00_re": to_unsigned(a[0][0][0], 32),
            "out_a00_im": to_unsigned(a[0][0][1], 32),
            "out_a01_re": to_unsigned(a[0][1][0], 32),
            "out_a01_im": to_unsigned(a[0][1][1], 32),
            "out_a02_re": to_unsigned(a[0][2][0], 32),
            "out_a02_im": to_unsigned(a[0][2][1], 32),
            "out_a03_re": to_unsigned(a[0][3][0], 32),
            "out_a03_im": to_unsigned(a[0][3][1], 32),
            "out_a10_re": to_unsigned(a[1][0][0], 32),
            "out_a10_im": to_unsigned(a[1][0][1], 32),
            "out_a11_re": to_unsigned(a[1][1][0], 32),
            "out_a11_im": to_unsigned(a[1][1][1], 32),
            "out_a12_re": to_unsigned(a[1][2][0], 32),
            "out_a12_im": to_unsigned(a[1][2][1], 32),
            "out_a13_re": to_unsigned(a[1][3][0], 32),
            "out_a13_im": to_unsigned(a[1][3][1], 32),
            "out_a20_re": to_unsigned(a[2][0][0], 32),
            "out_a20_im": to_unsigned(a[2][0][1], 32),
            "out_a21_re": to_unsigned(a[2][1][0], 32),
            "out_a21_im": to_unsigned(a[2][1][1], 32),
            "out_a22_re": to_unsigned(a[2][2][0], 32),
            "out_a22_im": to_unsigned(a[2][2][1], 32),
            "out_a23_re": to_unsigned(a[2][3][0], 32),
            "out_a23_im": to_unsigned(a[2][3][1], 32),
            "out_a30_re": to_unsigned(a[3][0][0], 32),
            "out_a30_im": to_unsigned(a[3][0][1], 32),
            "out_a31_re": to_unsigned(a[3][1][0], 32),
            "out_a31_im": to_unsigned(a[3][1][1], 32),
            "out_a32_re": to_unsigned(a[3][2][0], 32),
            "out_a32_im": to_unsigned(a[3][2][1], 32),
            "out_a33_re": to_unsigned(a[3][3][0], 32),
            "out_a33_im": to_unsigned(a[3][3][1], 32),
            "out_ipiv0": to_unsigned(ipiv[0], 2),
            "out_ipiv1": to_unsigned(ipiv[1], 2),
            "out_ipiv2": to_unsigned(ipiv[2], 2),
            "out_ipiv3": to_unsigned(ipiv[3], 2),
            "out_info": to_unsigned(info, 3),
        }