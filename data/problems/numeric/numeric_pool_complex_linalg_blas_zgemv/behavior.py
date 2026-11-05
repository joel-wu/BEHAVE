def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ComplexGemv4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_trans,
        in_alpha_re, in_alpha_im,
        in_beta_re, in_beta_im,
        in_a00_re, in_a00_im, in_a10_re, in_a10_im, in_a20_re, in_a20_im, in_a30_re, in_a30_im,
        in_a01_re, in_a01_im, in_a11_re, in_a11_im, in_a21_re, in_a21_im, in_a31_re, in_a31_im,
        in_a02_re, in_a02_im, in_a12_re, in_a12_im, in_a22_re, in_a22_im, in_a32_re, in_a32_im,
        in_a03_re, in_a03_im, in_a13_re, in_a13_im, in_a23_re, in_a23_im, in_a33_re, in_a33_im,
        in_x0_re, in_x0_im, in_x1_re, in_x1_im, in_x2_re, in_x2_im, in_x3_re, in_x3_im,
        in_y0_re, in_y0_im, in_y1_re, in_y1_im, in_y2_re, in_y2_im, in_y3_re, in_y3_im
    ):
        alpha = (to_signed(in_alpha_re, 64), to_signed(in_alpha_im, 64))
        beta = (to_signed(in_beta_re, 64), to_signed(in_beta_im, 64))

        A = [
            [(to_signed(in_a00_re, 64), to_signed(in_a00_im, 64)),
             (to_signed(in_a01_re, 64), to_signed(in_a01_im, 64)),
             (to_signed(in_a02_re, 64), to_signed(in_a02_im, 64)),
             (to_signed(in_a03_re, 64), to_signed(in_a03_im, 64))],
            [(to_signed(in_a10_re, 64), to_signed(in_a10_im, 64)),
             (to_signed(in_a11_re, 64), to_signed(in_a11_im, 64)),
             (to_signed(in_a12_re, 64), to_signed(in_a12_im, 64)),
             (to_signed(in_a13_re, 64), to_signed(in_a13_im, 64))],
            [(to_signed(in_a20_re, 64), to_signed(in_a20_im, 64)),
             (to_signed(in_a21_re, 64), to_signed(in_a21_im, 64)),
             (to_signed(in_a22_re, 64), to_signed(in_a22_im, 64)),
             (to_signed(in_a23_re, 64), to_signed(in_a23_im, 64))],
            [(to_signed(in_a30_re, 64), to_signed(in_a30_im, 64)),
             (to_signed(in_a31_re, 64), to_signed(in_a31_im, 64)),
             (to_signed(in_a32_re, 64), to_signed(in_a32_im, 64)),
             (to_signed(in_a33_re, 64), to_signed(in_a33_im, 64))]
        ]

        x = [
            (to_signed(in_x0_re, 64), to_signed(in_x0_im, 64)),
            (to_signed(in_x1_re, 64), to_signed(in_x1_im, 64)),
            (to_signed(in_x2_re, 64), to_signed(in_x2_im, 64)),
            (to_signed(in_x3_re, 64), to_signed(in_x3_im, 64))
        ]

        y_in = [
            (to_signed(in_y0_re, 64), to_signed(in_y0_im, 64)),
            (to_signed(in_y1_re, 64), to_signed(in_y1_im, 64)),
            (to_signed(in_y2_re, 64), to_signed(in_y2_im, 64)),
            (to_signed(in_y3_re, 64), to_signed(in_y3_im, 64))
        ]

        def cmul(a, b):
            return (a[0] * b[0] - a[1] * b[1], a[0] * b[1] + a[1] * b[0])

        def cadd(a, b):
            return (a[0] + b[0], a[1] + b[1])

        def cconj(a):
            return (a[0], -a[1])

        if beta[0] == 1 and beta[1] == 0:
            y_work = [y_in[0], y_in[1], y_in[2], y_in[3]]
        elif beta[0] == 0 and beta[1] == 0:
            y_work = [(0, 0), (0, 0), (0, 0), (0, 0)]
        else:
            y_work = [
                cmul(beta, y_in[0]),
                cmul(beta, y_in[1]),
                cmul(beta, y_in[2]),
                cmul(beta, y_in[3])
            ]

        mode = in_trans & 0b11
        if not (alpha[0] == 0 and alpha[1] == 0):
            if mode == 0b01:
                for j in range(4):
                    temp = (0, 0)
                    for i in range(4):
                        temp = cadd(temp, cmul(A[i][j], x[i]))
                    y_work[j] = cadd(y_work[j], cmul(alpha, temp))
            elif mode == 0b10:
                for j in range(4):
                    temp = (0, 0)
                    for i in range(4):
                        temp = cadd(temp, cmul(cconj(A[i][j]), x[i]))
                    y_work[j] = cadd(y_work[j], cmul(alpha, temp))
            else:
                for j in range(4):
                    temp = cmul(alpha, x[j])
                    for i in range(4):
                        y_work[i] = cadd(y_work[i], cmul(temp, A[i][j]))

        return {
            "out_y0_re": to_unsigned(y_work[0][0], 64),
            "out_y0_im": to_unsigned(y_work[0][1], 64),
            "out_y1_re": to_unsigned(y_work[1][0], 64),
            "out_y1_im": to_unsigned(y_work[1][1], 64),
            "out_y2_re": to_unsigned(y_work[2][0], 64),
            "out_y2_im": to_unsigned(y_work[2][1], 64),
            "out_y3_re": to_unsigned(y_work[3][0], 64),
            "out_y3_im": to_unsigned(y_work[3][1], 64),
        }