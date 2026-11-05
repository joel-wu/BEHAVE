def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat32(v):
    if v > 2147483647:
        return 2147483647
    if v < -2147483648:
        return -2147483648
    return v


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def trunc_div_pow2_toward_zero(v, s):
    if v >= 0:
        return v >> s
    return -((-v) >> s)


def trunc_div_toward_zero(n, d):
    if d == 0:
        return 0
    sign_neg = (n < 0) ^ (d < 0)
    q = (n if n >= 0 else -n) // (d if d >= 0 else -d)
    return -q if sign_neg else q


def mul_q31_q15(a_q31, b_q15):
    return sat32(trunc_div_pow2_toward_zero(a_q31 * b_q15, 15))


def mul_q15_q15(a_q15, b_q15):
    return sat16(trunc_div_pow2_toward_zero(a_q15 * b_q15, 15))


def mul_q31_q31(a_q31, b_q31):
    return sat32(trunc_div_pow2_toward_zero(a_q31 * b_q31, 31))


def div_q31_to_q15(num_q31, den_q31):
    if den_q31 == 0:
        return 32767 if num_q31 >= 0 else -32768
    return sat16(trunc_div_toward_zero(num_q31 << 15, den_q31))


class ControlKalmanKalman1dStateUpdate:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x_prior, in_p_prior, in_z, in_h, in_r):
        x_prior = to_signed(in_x_prior, 32)
        p_prior = to_signed(in_p_prior, 32)
        z = to_signed(in_z, 32)
        h = to_signed(in_h, 16)
        r = to_signed(in_r, 32)

        hx_q31 = mul_q31_q15(x_prior, h)
        y_q31 = sat32(z - hx_q31)

        ph_q31 = mul_q31_q15(p_prior, h)
        hph_q31 = mul_q31_q15(ph_q31, h)
        s_q31 = sat32(hph_q31 + r)

        k_q15 = div_q31_to_q15(ph_q31, s_q31)

        ky_q31 = mul_q31_q15(y_q31, k_q15)
        x_post_q31 = sat32(x_prior + ky_q31)

        kh_q15 = mul_q15_q15(k_q15, h)
        i_minus_kh_q15 = 32768 - kh_q15
        i_minus_kh_sq_q15 = (i_minus_kh_q15 * i_minus_kh_q15) >> 15
        term1_q31 = mul_q31_q15(p_prior, i_minus_kh_sq_q15)
        k_sq_q15 = (k_q15 * k_q15) >> 15
        term2_q31 = mul_q31_q15(r, k_sq_q15)
        p_post_q31 = sat32(term1_q31 + term2_q31)

        return {
            "out_x_post": to_unsigned(x_post_q31, 32),
            "out_p_post": to_unsigned(p_post_q31, 32),
            "out_y": to_unsigned(y_q31, 32),
            "out_s": to_unsigned(s_q31, 32),
            "out_k": to_unsigned(k_q15, 16),
        }
