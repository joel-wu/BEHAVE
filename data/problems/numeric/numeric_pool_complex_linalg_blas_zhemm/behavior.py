def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class HermitianMatrixMultiplyTile:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_uplo,
        in_alpha_re,
        in_alpha_im,
        in_beta_re,
        in_beta_im,
        in_a00_re,
        in_a00_im,
        in_a01_re,
        in_a01_im,
        in_a10_re,
        in_a10_im,
        in_a11_re,
        in_a11_im,
        in_b00_re,
        in_b00_im,
        in_b01_re,
        in_b01_im,
        in_b10_re,
        in_b10_im,
        in_b11_re,
        in_b11_im,
        in_c00_re,
        in_c00_im,
        in_c01_re,
        in_c01_im,
        in_c10_re,
        in_c10_im,
        in_c11_re,
        in_c11_im,
    ):
        alpha_re = to_signed(in_alpha_re, 32)
        alpha_im = to_signed(in_alpha_im, 32)
        beta_re = to_signed(in_beta_re, 32)
        beta_im = to_signed(in_beta_im, 32)

        a00_re = to_signed(in_a00_re, 32)
        a01_re = to_signed(in_a01_re, 32)
        a01_im = to_signed(in_a01_im, 32)
        a10_re = to_signed(in_a10_re, 32)
        a10_im = to_signed(in_a10_im, 32)
        a11_re = to_signed(in_a11_re, 32)

        b00_re = to_signed(in_b00_re, 32)
        b00_im = to_signed(in_b00_im, 32)
        b01_re = to_signed(in_b01_re, 32)
        b01_im = to_signed(in_b01_im, 32)
        b10_re = to_signed(in_b10_re, 32)
        b10_im = to_signed(in_b10_im, 32)
        b11_re = to_signed(in_b11_re, 32)
        b11_im = to_signed(in_b11_im, 32)

        c00_re = to_signed(in_c00_re, 32)
        c00_im = to_signed(in_c00_im, 32)
        c01_re = to_signed(in_c01_re, 32)
        c01_im = to_signed(in_c01_im, 32)
        c10_re = to_signed(in_c10_re, 32)
        c10_im = to_signed(in_c10_im, 32)
        c11_re = to_signed(in_c11_re, 32)
        c11_im = to_signed(in_c11_im, 32)

        A00_re, A00_im = a00_re, 0
        A11_re, A11_im = a11_re, 0
        if in_uplo:
            A01_re, A01_im = a01_re, a01_im
            A10_re, A10_im = a01_re, -a01_im
        else:
            A10_re, A10_im = a10_re, a10_im
            A01_re, A01_im = a10_re, -a10_im

        def cmul(x_re, x_im, y_re, y_im):
            return x_re * y_re - x_im * y_im, x_re * y_im + x_im * y_re

        def cadd(x_re, x_im, y_re, y_im):
            return x_re + y_re, x_im + y_im

        if in_side == 0:
            p00_re0, p00_im0 = cmul(A00_re, A00_im, b00_re, b00_im)
            p00_re1, p00_im1 = cmul(A01_re, A01_im, b10_re, b10_im)
            p00_re, p00_im = cadd(p00_re0, p00_im0, p00_re1, p00_im1)

            p01_re0, p01_im0 = cmul(A00_re, A00_im, b01_re, b01_im)
            p01_re1, p01_im1 = cmul(A01_re, A01_im, b11_re, b11_im)
            p01_re, p01_im = cadd(p01_re0, p01_im0, p01_re1, p01_im1)

            p10_re0, p10_im0 = cmul(A10_re, A10_im, b00_re, b00_im)
            p10_re1, p10_im1 = cmul(A11_re, A11_im, b10_re, b10_im)
            p10_re, p10_im = cadd(p10_re0, p10_im0, p10_re1, p10_im1)

            p11_re0, p11_im0 = cmul(A10_re, A10_im, b01_re, b01_im)
            p11_re1, p11_im1 = cmul(A11_re, A11_im, b11_re, b11_im)
            p11_re, p11_im = cadd(p11_re0, p11_im0, p11_re1, p11_im1)
        else:
            p00_re0, p00_im0 = cmul(b00_re, b00_im, A00_re, A00_im)
            p00_re1, p00_im1 = cmul(b01_re, b01_im, A10_re, A10_im)
            p00_re, p00_im = cadd(p00_re0, p00_im0, p00_re1, p00_im1)

            p01_re0, p01_im0 = cmul(b00_re, b00_im, A01_re, A01_im)
            p01_re1, p01_im1 = cmul(b01_re, b01_im, A11_re, A11_im)
            p01_re, p01_im = cadd(p01_re0, p01_im0, p01_re1, p01_im1)

            p10_re0, p10_im0 = cmul(b10_re, b10_im, A00_re, A00_im)
            p10_re1, p10_im1 = cmul(b11_re, b11_im, A10_re, A10_im)
            p10_re, p10_im = cadd(p10_re0, p10_im0, p10_re1, p10_im1)

            p11_re0, p11_im0 = cmul(b10_re, b10_im, A01_re, A01_im)
            p11_re1, p11_im1 = cmul(b11_re, b11_im, A11_re, A11_im)
            p11_re, p11_im = cadd(p11_re0, p11_im0, p11_re1, p11_im1)

        ap00_re, ap00_im = cmul(alpha_re, alpha_im, p00_re, p00_im)
        ap01_re, ap01_im = cmul(alpha_re, alpha_im, p01_re, p01_im)
        ap10_re, ap10_im = cmul(alpha_re, alpha_im, p10_re, p10_im)
        ap11_re, ap11_im = cmul(alpha_re, alpha_im, p11_re, p11_im)

        bc00_re, bc00_im = cmul(beta_re, beta_im, c00_re, c00_im)
        bc01_re, bc01_im = cmul(beta_re, beta_im, c01_re, c01_im)
        bc10_re, bc10_im = cmul(beta_re, beta_im, c10_re, c10_im)
        bc11_re, bc11_im = cmul(beta_re, beta_im, c11_re, c11_im)

        out00_re, out00_im = cadd(ap00_re, ap00_im, bc00_re, bc00_im)
        out01_re, out01_im = cadd(ap01_re, ap01_im, bc01_re, bc01_im)
        out10_re, out10_im = cadd(ap10_re, ap10_im, bc10_re, bc10_im)
        out11_re, out11_im = cadd(ap11_re, ap11_im, bc11_re, bc11_im)

        return {
            "out_c00_re": to_unsigned(out00_re, 32),
            "out_c00_im": to_unsigned(out00_im, 32),
            "out_c01_re": to_unsigned(out01_re, 32),
            "out_c01_im": to_unsigned(out01_im, 32),
            "out_c10_re": to_unsigned(out10_re, 32),
            "out_c10_im": to_unsigned(out10_im, 32),
            "out_c11_re": to_unsigned(out11_re, 32),
            "out_c11_im": to_unsigned(out11_im, 32),
        }