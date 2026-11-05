def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def trunc0(num, den):
    if den == 0:
        return 0
    neg = (num < 0) ^ (den < 0)
    anum = -num if num < 0 else num
    aden = -den if den < 0 else den
    q = anum // aden
    return -q if neg else q


def isqrt_u64(n):
    lo = 0
    hi = 1 << 32
    for _ in range(32):
        mid = (lo + hi) >> 1
        if mid * mid <= n:
            lo = mid
        else:
            hi = mid
    for _ in range(32):
        mid = (lo + hi) >> 1
        if mid * mid <= n:
            lo = mid
        else:
            hi = mid
    return lo


class ComplexHouseholderReflector3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_alpha_re,
        in_alpha_im,
        in_x0_re,
        in_x0_im,
        in_x1_re,
        in_x1_im,
        in_x2_re,
        in_x2_im,
    ):
        alphar = to_signed(in_alpha_re, 16)
        alphai = to_signed(in_alpha_im, 16)
        x0r = to_signed(in_x0_re, 16)
        x0i = to_signed(in_x0_im, 16)
        x1r = to_signed(in_x1_re, 16)
        x1i = to_signed(in_x1_im, 16)
        x2r = to_signed(in_x2_re, 16)
        x2i = to_signed(in_x2_im, 16)

        xnorm_sq = x0r * x0r + x0i * x0i + x1r * x1r + x1i * x1i + x2r * x2r + x2i * x2i

        if xnorm_sq == 0 and alphai == 0:
            return {
                "out_alpha_re": to_unsigned(alphar, 16),
                "out_alpha_im": to_unsigned(alphai, 16),
                "out_tau_re": 0,
                "out_tau_im": 0,
                "out_x0_re": to_unsigned(x0r, 16),
                "out_x0_im": to_unsigned(x0i, 16),
                "out_x1_re": to_unsigned(x1r, 16),
                "out_x1_im": to_unsigned(x1i, 16),
                "out_x2_re": to_unsigned(x2r, 16),
                "out_x2_im": to_unsigned(x2i, 16),
            }

        beta_arg = alphar * alphar + alphai * alphai + xnorm_sq
        beta_mag = isqrt_u64(beta_arg)
        beta = -beta_mag if alphar >= 0 else beta_mag

        tau_re = trunc0((beta - alphar) * 8192, beta)
        tau_im = trunc0(-alphai * 8192, beta)

        den = (alphar - beta) * (alphar - beta) + alphai * alphai
        v0_re = trunc0((x0r * (alphar - beta) + x0i * alphai) * 8192, den)
        v0_im = trunc0((x0i * (alphar - beta) - x0r * alphai) * 8192, den)
        v1_re = trunc0((x1r * (alphar - beta) + x1i * alphai) * 8192, den)
        v1_im = trunc0((x1i * (alphar - beta) - x1r * alphai) * 8192, den)
        v2_re = trunc0((x2r * (alphar - beta) + x2i * alphai) * 8192, den)
        v2_im = trunc0((x2i * (alphar - beta) - x2r * alphai) * 8192, den)

        return {
            "out_alpha_re": to_unsigned(sat16(beta), 16),
            "out_alpha_im": 0,
            "out_tau_re": to_unsigned(sat16(tau_re), 16),
            "out_tau_im": to_unsigned(sat16(tau_im), 16),
            "out_x0_re": to_unsigned(sat16(v0_re), 16),
            "out_x0_im": to_unsigned(sat16(v0_im), 16),
            "out_x1_re": to_unsigned(sat16(v1_re), 16),
            "out_x1_im": to_unsigned(sat16(v1_im), 16),
            "out_x2_re": to_unsigned(sat16(v2_re), 16),
            "out_x2_im": to_unsigned(sat16(v2_im), 16),
        }
