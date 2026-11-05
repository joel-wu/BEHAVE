def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap16(v):
    return to_signed(v, 16)


def qmul(x, y):
    return wrap16((wrap16(x) * wrap16(y)) >> 12)


def cconj(re, im):
    return wrap16(re), wrap16(-wrap16(im))


def cmul(ar, ai, br, bi):
    return wrap16(qmul(ar, br) - qmul(ai, bi)), wrap16(qmul(ar, bi) + qmul(ai, br))


def cadd(ar, ai, br, bi):
    return wrap16(ar + br), wrap16(ai + bi)


def complex_is_zero(re, im):
    return wrap16(re) == 0 and wrap16(im) == 0


def real_conj_self(re, im):
    return wrap16(qmul(re, re) + qmul(im, im))


class HermitianRankkTile:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_trans,
        in_alpha,
        in_beta,
        in_a00_re,
        in_a00_im,
        in_a01_re,
        in_a01_im,
        in_a10_re,
        in_a10_im,
        in_a11_re,
        in_a11_im,
        in_c00_re,
        in_c00_im,
        in_c01_re,
        in_c01_im,
        in_c10_re,
        in_c10_im,
        in_c11_re,
        in_c11_im,
    ):
        alpha = wrap16(in_alpha)
        beta = wrap16(in_beta)

        a00_re = wrap16(in_a00_re)
        a00_im = wrap16(in_a00_im)
        a01_re = wrap16(in_a01_re)
        a01_im = wrap16(in_a01_im)
        a10_re = wrap16(in_a10_re)
        a10_im = wrap16(in_a10_im)
        a11_re = wrap16(in_a11_re)
        a11_im = wrap16(in_a11_im)

        c00_re = wrap16(in_c00_re)
        c01_re = wrap16(in_c01_re)
        c01_im = wrap16(in_c01_im)
        c10_re = wrap16(in_c10_re)
        c10_im = wrap16(in_c10_im)
        c11_re = wrap16(in_c11_re)

        beta_zero = beta == 0
        beta_one = beta == 0x1000

        out_c00_re = 0
        out_c01_re = 0
        out_c01_im = 0
        out_c10_re = 0
        out_c10_im = 0
        out_c11_re = 0

        if in_trans == 0:
            if in_uplo == 1:
                if beta_zero:
                    acc00 = 0
                else:
                    acc00 = qmul(beta, c00_re)
                if not complex_is_zero(a00_re, a00_im):
                    acc00 = wrap16(acc00 + qmul(alpha, real_conj_self(a00_re, a00_im)))
                if not complex_is_zero(a01_re, a01_im):
                    acc00 = wrap16(acc00 + qmul(alpha, real_conj_self(a01_re, a01_im)))
                out_c00_re = acc00

                if beta_zero:
                    acc01_re = 0
                    acc01_im = 0
                elif beta_one:
                    acc01_re = c01_re
                    acc01_im = c01_im
                else:
                    acc01_re = qmul(beta, c01_re)
                    acc01_im = qmul(beta, c01_im)
                if not complex_is_zero(a10_re, a10_im):
                    temp_re, temp_im = cconj(a10_re, a10_im)
                    temp_re = qmul(alpha, temp_re)
                    temp_im = qmul(alpha, temp_im)
                    prod_re, prod_im = cmul(temp_re, temp_im, a00_re, a00_im)
                    acc01_re, acc01_im = cadd(acc01_re, acc01_im, prod_re, prod_im)
                if not complex_is_zero(a11_re, a11_im):
                    temp_re, temp_im = cconj(a11_re, a11_im)
                    temp_re = qmul(alpha, temp_re)
                    temp_im = qmul(alpha, temp_im)
                    prod_re, prod_im = cmul(temp_re, temp_im, a01_re, a01_im)
                    acc01_re, acc01_im = cadd(acc01_re, acc01_im, prod_re, prod_im)
                out_c01_re = acc01_re
                out_c01_im = acc01_im

                if beta_zero:
                    acc11 = 0
                elif beta_one:
                    acc11 = c11_re
                else:
                    acc11 = qmul(beta, c11_re)
                if not complex_is_zero(a10_re, a10_im):
                    acc11 = wrap16(acc11 + qmul(alpha, real_conj_self(a10_re, a10_im)))
                if not complex_is_zero(a11_re, a11_im):
                    acc11 = wrap16(acc11 + qmul(alpha, real_conj_self(a11_re, a11_im)))
                out_c11_re = acc11
            else:
                if beta_zero:
                    acc00 = 0
                elif beta_one:
                    acc00 = c00_re
                else:
                    acc00 = qmul(beta, c00_re)
                if not complex_is_zero(a00_re, a00_im):
                    acc00 = wrap16(acc00 + qmul(alpha, real_conj_self(a00_re, a00_im)))
                if not complex_is_zero(a01_re, a01_im):
                    acc00 = wrap16(acc00 + qmul(alpha, real_conj_self(a01_re, a01_im)))
                out_c00_re = acc00

                if beta_zero:
                    acc10_re = 0
                    acc10_im = 0
                elif beta_one:
                    acc10_re = c10_re
                    acc10_im = c10_im
                else:
                    acc10_re = qmul(beta, c10_re)
                    acc10_im = qmul(beta, c10_im)
                if not complex_is_zero(a00_re, a00_im):
                    temp_re, temp_im = cconj(a00_re, a00_im)
                    temp_re = qmul(alpha, temp_re)
                    temp_im = qmul(alpha, temp_im)
                    prod_re, prod_im = cmul(temp_re, temp_im, a10_re, a10_im)
                    acc10_re, acc10_im = cadd(acc10_re, acc10_im, prod_re, prod_im)
                if not complex_is_zero(a01_re, a01_im):
                    temp_re, temp_im = cconj(a01_re, a01_im)
                    temp_re = qmul(alpha, temp_re)
                    temp_im = qmul(alpha, temp_im)
                    prod_re, prod_im = cmul(temp_re, temp_im, a11_re, a11_im)
                    acc10_re, acc10_im = cadd(acc10_re, acc10_im, prod_re, prod_im)
                out_c10_re = acc10_re
                out_c10_im = acc10_im

                if beta_zero:
                    acc11 = 0
                elif beta_one:
                    acc11 = c11_re
                else:
                    acc11 = qmul(beta, c11_re)
                if not complex_is_zero(a10_re, a10_im):
                    acc11 = wrap16(acc11 + qmul(alpha, real_conj_self(a10_re, a10_im)))
                if not complex_is_zero(a11_re, a11_im):
                    acc11 = wrap16(acc11 + qmul(alpha, real_conj_self(a11_re, a11_im)))
                out_c11_re = acc11
        else:
            rtemp00 = wrap16(real_conj_self(a00_re, a00_im) + real_conj_self(a10_re, a10_im))
            rtemp11 = wrap16(real_conj_self(a01_re, a01_im) + real_conj_self(a11_re, a11_im))

            if in_uplo == 1:
                if beta_zero:
                    out_c00_re = qmul(alpha, rtemp00)
                else:
                    out_c00_re = wrap16(qmul(alpha, rtemp00) + qmul(beta, c00_re))

                conj_re, conj_im = cconj(a00_re, a00_im)
                m0_re, m0_im = cmul(conj_re, conj_im, a01_re, a01_im)
                conj_re, conj_im = cconj(a10_re, a10_im)
                m1_re, m1_im = cmul(conj_re, conj_im, a11_re, a11_im)
                temp01_re, temp01_im = cadd(m0_re, m0_im, m1_re, m1_im)
                s01_re = qmul(alpha, temp01_re)
                s01_im = qmul(alpha, temp01_im)
                if beta_zero:
                    out_c01_re = s01_re
                    out_c01_im = s01_im
                else:
                    out_c01_re = wrap16(s01_re + qmul(beta, c01_re))
                    out_c01_im = wrap16(s01_im + qmul(beta, c01_im))

                if beta_zero:
                    out_c11_re = qmul(alpha, rtemp11)
                else:
                    out_c11_re = wrap16(qmul(alpha, rtemp11) + qmul(beta, c11_re))
            else:
                if beta_zero:
                    out_c00_re = qmul(alpha, rtemp00)
                else:
                    out_c00_re = wrap16(qmul(alpha, rtemp00) + qmul(beta, c00_re))

                conj_re, conj_im = cconj(a01_re, a01_im)
                m0_re, m0_im = cmul(conj_re, conj_im, a00_re, a00_im)
                conj_re, conj_im = cconj(a11_re, a11_im)
                m1_re, m1_im = cmul(conj_re, conj_im, a10_re, a10_im)
                temp10_re, temp10_im = cadd(m0_re, m0_im, m1_re, m1_im)
                s10_re = qmul(alpha, temp10_re)
                s10_im = qmul(alpha, temp10_im)
                if beta_zero:
                    out_c10_re = s10_re
                    out_c10_im = s10_im
                else:
                    out_c10_re = wrap16(s10_re + qmul(beta, c10_re))
                    out_c10_im = wrap16(s10_im + qmul(beta, c10_im))

                if beta_zero:
                    out_c11_re = qmul(alpha, rtemp11)
                else:
                    out_c11_re = wrap16(qmul(alpha, rtemp11) + qmul(beta, c11_re))

        return {
            "out_c00_re": to_unsigned(out_c00_re, 16),
            "out_c00_im": 0,
            "out_c01_re": to_unsigned(out_c01_re if in_uplo == 1 else 0, 16),
            "out_c01_im": to_unsigned(out_c01_im if in_uplo == 1 else 0, 16),
            "out_c10_re": to_unsigned(out_c10_re if in_uplo == 0 else 0, 16),
            "out_c10_im": to_unsigned(out_c10_im if in_uplo == 0 else 0, 16),
            "out_c11_re": to_unsigned(out_c11_re, 16),
            "out_c11_im": 0,
        }
