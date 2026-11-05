def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class HermitianRank2kTile:
    def __init__(self):
        self.reset()

    def reset(self):
        self.uplo = 0
        self.trans = 0
        self.alpha_re = 0
        self.alpha_im = 0
        self.alpha_conj_re = 0
        self.alpha_conj_im = 0
        self.beta = 0
        self.c00_re = 0
        self.c00_im = 0
        self.c01_re = 0
        self.c01_im = 0
        self.c10_re = 0
        self.c10_im = 0
        self.c11_re = 0
        self.c11_im = 0

    def process(
        self,
        in_start,
        in_last,
        in_uplo,
        in_trans,
        in_alpha_re,
        in_alpha_im,
        in_beta,
        in_c00_re,
        in_c00_im,
        in_c01_re,
        in_c01_im,
        in_c10_re,
        in_c10_im,
        in_c11_re,
        in_c11_im,
        in_a0_re,
        in_a0_im,
        in_a1_re,
        in_a1_im,
        in_b0_re,
        in_b0_im,
        in_b1_re,
        in_b1_im,
    ):
        if in_start:
            self.uplo = in_uplo
            self.trans = in_trans
            self.alpha_re = to_signed(in_alpha_re, 32)
            self.alpha_im = to_signed(in_alpha_im, 32)
            self.alpha_conj_re = self.alpha_re
            self.alpha_conj_im = -self.alpha_im
            self.beta = to_signed(in_beta, 32)
            self.c00_re = to_signed(in_c00_re, 32)
            self.c00_im = 0
            self.c01_re = to_signed(in_c01_re, 32)
            self.c01_im = to_signed(in_c01_im, 32)
            self.c10_re = to_signed(in_c10_re, 32)
            self.c10_im = to_signed(in_c10_im, 32)
            self.c11_re = to_signed(in_c11_re, 32)
            self.c11_im = 0
            if self.uplo:
                if self.beta == 0:
                    self.c00_re = 0
                    self.c00_im = 0
                    self.c01_re = 0
                    self.c01_im = 0
                    self.c11_re = 0
                    self.c11_im = 0
                elif self.beta != 1:
                    self.c00_re = self.beta * self.c00_re
                    self.c00_im = 0
                    self.c01_re = self.beta * self.c01_re
                    self.c01_im = self.beta * self.c01_im
                    self.c11_re = self.beta * self.c11_re
                    self.c11_im = 0
                else:
                    self.c00_im = 0
                    self.c11_im = 0
            else:
                if self.beta == 0:
                    self.c00_re = 0
                    self.c00_im = 0
                    self.c10_re = 0
                    self.c10_im = 0
                    self.c11_re = 0
                    self.c11_im = 0
                elif self.beta != 1:
                    self.c00_re = self.beta * self.c00_re
                    self.c00_im = 0
                    self.c10_re = self.beta * self.c10_re
                    self.c10_im = self.beta * self.c10_im
                    self.c11_re = self.beta * self.c11_re
                    self.c11_im = 0
                else:
                    self.c00_im = 0
                    self.c11_im = 0

        a0_re = to_signed(in_a0_re, 32)
        a0_im = to_signed(in_a0_im, 32)
        a1_re = to_signed(in_a1_re, 32)
        a1_im = to_signed(in_a1_im, 32)
        b0_re = to_signed(in_b0_re, 32)
        b0_im = to_signed(in_b0_im, 32)
        b1_re = to_signed(in_b1_re, 32)
        b1_im = to_signed(in_b1_im, 32)

        if self.trans == 0:
            if (a0_re != 0) or (a0_im != 0) or (b0_re != 0) or (b0_im != 0):
                temp1_0_re = self.alpha_re * b0_re + self.alpha_im * b0_im
                temp1_0_im = self.alpha_im * b0_re - self.alpha_re * b0_im
                temp2_0_re = self.alpha_conj_re * a0_re + self.alpha_conj_im * a0_im
                temp2_0_im = self.alpha_conj_im * a0_re - self.alpha_conj_re * a0_im
                diag0_re = a0_re * temp1_0_re - a0_im * temp1_0_im + b0_re * temp2_0_re - b0_im * temp2_0_im
                self.c00_re = self.c00_re + diag0_re
                self.c00_im = 0
                if self.uplo == 0:
                    add01_re = a1_re * temp1_0_re - a1_im * temp1_0_im + b1_re * temp2_0_re - b1_im * temp2_0_im
                    add01_im = a1_re * temp1_0_im + a1_im * temp1_0_re + b1_re * temp2_0_im + b1_im * temp2_0_re
                    self.c10_re = self.c10_re + add01_re
                    self.c10_im = self.c10_im + add01_im
            if (a1_re != 0) or (a1_im != 0) or (b1_re != 0) or (b1_im != 0):
                temp1_1_re = self.alpha_re * b1_re + self.alpha_im * b1_im
                temp1_1_im = self.alpha_im * b1_re - self.alpha_re * b1_im
                temp2_1_re = self.alpha_conj_re * a1_re + self.alpha_conj_im * a1_im
                temp2_1_im = self.alpha_conj_im * a1_re - self.alpha_conj_re * a1_im
                diag1_re = a1_re * temp1_1_re - a1_im * temp1_1_im + b1_re * temp2_1_re - b1_im * temp2_1_im
                self.c11_re = self.c11_re + diag1_re
                self.c11_im = 0
                if self.uplo:
                    add10_re = a0_re * temp1_1_re - a0_im * temp1_1_im + b0_re * temp2_1_re - b0_im * temp2_1_im
                    add10_im = a0_re * temp1_1_im + a0_im * temp1_1_re + b0_re * temp2_1_im + b0_im * temp2_1_re
                    self.c01_re = self.c01_re + add10_re
                    self.c01_im = self.c01_im + add10_im
        else:
            t00_0_re = a0_re * b0_re + a0_im * b0_im
            t00_0_im = -a0_im * b0_re + a0_re * b0_im
            u00_0_re = b0_re * a0_re + b0_im * a0_im
            u00_0_im = -b0_im * a0_re + b0_re * a0_im
            add00_re = self.alpha_re * t00_0_re - self.alpha_im * t00_0_im + self.alpha_conj_re * u00_0_re - self.alpha_conj_im * u00_0_im
            self.c00_re = self.c00_re + add00_re
            self.c00_im = 0

            t11_0_re = a1_re * b1_re + a1_im * b1_im
            t11_0_im = -a1_im * b1_re + a1_re * b1_im
            u11_0_re = b1_re * a1_re + b1_im * a1_im
            u11_0_im = -b1_im * a1_re + b1_re * a1_im
            add11_re = self.alpha_re * t11_0_re - self.alpha_im * t11_0_im + self.alpha_conj_re * u11_0_re - self.alpha_conj_im * u11_0_im
            self.c11_re = self.c11_re + add11_re
            self.c11_im = 0

            if self.uplo:
                t01_re = a0_re * b1_re + a0_im * b1_im
                t01_im = -a0_im * b1_re + a0_re * b1_im
                u01_re = b0_re * a1_re + b0_im * a1_im
                u01_im = -b0_im * a1_re + b0_re * a1_im
                add01_re = self.alpha_re * t01_re - self.alpha_im * t01_im + self.alpha_conj_re * u01_re - self.alpha_conj_im * u01_im
                add01_im = self.alpha_re * t01_im + self.alpha_im * t01_re + self.alpha_conj_re * u01_im + self.alpha_conj_im * u01_re
                self.c01_re = self.c01_re + add01_re
                self.c01_im = self.c01_im + add01_im
            else:
                t10_re = a1_re * b0_re + a1_im * b0_im
                t10_im = -a1_im * b0_re + a1_re * b0_im
                u10_re = b1_re * a0_re + b1_im * a0_im
                u10_im = -b1_im * a0_re + b1_re * a0_im
                add10_re = self.alpha_re * t10_re - self.alpha_im * t10_im + self.alpha_conj_re * u10_re - self.alpha_conj_im * u10_im
                add10_im = self.alpha_re * t10_im + self.alpha_im * t10_re + self.alpha_conj_re * u10_im + self.alpha_conj_im * u10_re
                self.c10_re = self.c10_re + add10_re
                self.c10_im = self.c10_im + add10_im

        if not in_last:
            return None

        if self.uplo:
            out_c01_re = self.c01_re
            out_c01_im = self.c01_im
            out_c10_re = self.c01_re
            out_c10_im = -self.c01_im
        else:
            out_c10_re = self.c10_re
            out_c10_im = self.c10_im
            out_c01_re = self.c10_re
            out_c01_im = -self.c10_im

        return {
            "out_uplo": to_unsigned(self.uplo, 1),
            "out_c00_re": to_unsigned(self.c00_re, 32),
            "out_c00_im": to_unsigned(0, 32),
            "out_c01_re": to_unsigned(out_c01_re, 32),
            "out_c01_im": to_unsigned(out_c01_im, 32),
            "out_c10_re": to_unsigned(out_c10_re, 32),
            "out_c10_im": to_unsigned(out_c10_im, 32),
            "out_c11_re": to_unsigned(self.c11_re, 32),
            "out_c11_im": to_unsigned(0, 32),
        }
