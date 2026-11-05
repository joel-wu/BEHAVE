def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class HermitianMatrixVectorStep4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_alpha_re, in_alpha_im,
        in_beta_re, in_beta_im,
        in_x0_re, in_x0_im,
        in_x1_re, in_x1_im,
        in_x2_re, in_x2_im,
        in_x3_re, in_x3_im,
        in_y0_re, in_y0_im,
        in_y1_re, in_y1_im,
        in_y2_re, in_y2_im,
        in_y3_re, in_y3_im,
        in_a00_re, in_a00_im,
        in_a01_re, in_a01_im,
        in_a02_re, in_a02_im,
        in_a03_re, in_a03_im,
        in_a11_re, in_a11_im,
        in_a12_re, in_a12_im,
        in_a13_re, in_a13_im,
        in_a22_re, in_a22_im,
        in_a23_re, in_a23_im,
        in_a33_re, in_a33_im
    ):
        alpha = (to_signed(in_alpha_re, 32), to_signed(in_alpha_im, 32))
        beta = (to_signed(in_beta_re, 32), to_signed(in_beta_im, 32))

        x = [
            (to_signed(in_x0_re, 32), to_signed(in_x0_im, 32)),
            (to_signed(in_x1_re, 32), to_signed(in_x1_im, 32)),
            (to_signed(in_x2_re, 32), to_signed(in_x2_im, 32)),
            (to_signed(in_x3_re, 32), to_signed(in_x3_im, 32)),
        ]

        y_in = [
            (to_signed(in_y0_re, 32), to_signed(in_y0_im, 32)),
            (to_signed(in_y1_re, 32), to_signed(in_y1_im, 32)),
            (to_signed(in_y2_re, 32), to_signed(in_y2_im, 32)),
            (to_signed(in_y3_re, 32), to_signed(in_y3_im, 32)),
        ]

        a00 = (to_signed(in_a00_re, 32), 0)
        a11 = (to_signed(in_a11_re, 32), 0)
        a22 = (to_signed(in_a22_re, 32), 0)
        a33 = (to_signed(in_a33_re, 32), 0)
        a01 = (to_signed(in_a01_re, 32), to_signed(in_a01_im, 32))
        a02 = (to_signed(in_a02_re, 32), to_signed(in_a02_im, 32))
        a03 = (to_signed(in_a03_re, 32), to_signed(in_a03_im, 32))
        a12 = (to_signed(in_a12_re, 32), to_signed(in_a12_im, 32))
        a13 = (to_signed(in_a13_re, 32), to_signed(in_a13_im, 32))
        a23 = (to_signed(in_a23_re, 32), to_signed(in_a23_im, 32))

        def conj(z):
            return (z[0], -z[1])

        A = [[(0, 0) for _ in range(4)] for _ in range(4)]
        A[0][0] = a00
        A[1][1] = a11
        A[2][2] = a22
        A[3][3] = a33

        if in_uplo & 1:
            A[0][1] = a01
            A[0][2] = a02
            A[0][3] = a03
            A[1][2] = a12
            A[1][3] = a13
            A[2][3] = a23
            A[1][0] = conj(a01)
            A[2][0] = conj(a02)
            A[3][0] = conj(a03)
            A[2][1] = conj(a12)
            A[3][1] = conj(a13)
            A[3][2] = conj(a23)
        else:
            A[1][0] = a01
            A[2][0] = a02
            A[3][0] = a03
            A[2][1] = a12
            A[3][1] = a13
            A[3][2] = a23
            A[0][1] = conj(a01)
            A[0][2] = conj(a02)
            A[0][3] = conj(a03)
            A[1][2] = conj(a12)
            A[1][3] = conj(a13)
            A[2][3] = conj(a23)

        def cmul(a, b):
            return (a[0] * b[0] - a[1] * b[1], a[0] * b[1] + a[1] * b[0])

        def cadd(a, b):
            return (a[0] + b[0], a[1] + b[1])

        y_out = []
        for j in range(4):
            t0 = cmul(A[j][0], x[0])
            t1 = cmul(A[j][1], x[1])
            t2 = cmul(A[j][2], x[2])
            t3 = cmul(A[j][3], x[3])
            mv = cadd(cadd(cadd(t0, t1), t2), t3)
            p = cmul(alpha, mv)
            q = cmul(beta, y_in[j])
            y_out.append(cadd(p, q))

        return {
            "out_y0_re": to_unsigned(y_out[0][0], 32),
            "out_y0_im": to_unsigned(y_out[0][1], 32),
            "out_y1_re": to_unsigned(y_out[1][0], 32),
            "out_y1_im": to_unsigned(y_out[1][1], 32),
            "out_y2_re": to_unsigned(y_out[2][0], 32),
            "out_y2_im": to_unsigned(y_out[2][1], 32),
            "out_y3_re": to_unsigned(y_out[3][0], 32),
            "out_y3_im": to_unsigned(y_out[3][1], 32),
        }