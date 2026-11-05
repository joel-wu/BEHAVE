class ControlKalmanKalman1dGainUpdate:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_p, in_h, in_r):
        p = to_signed(in_p, 32)
        h = to_signed(in_h, 32)
        r = to_signed(in_r, 32)

        hp_q31 = sat32((h * p) >> 31)
        hph_q31 = sat32((h * hp_q31) >> 31)
        s = sat32(hph_q31 + r)

        if s == 0:
            si = 0
            k = 0
        else:
            si = sat32(sdiv_trunc_zero(1 << 62, s))
            k = sat32(sdiv_trunc_zero(hp_q31 << 31, s))

        return {
            "out_s": to_unsigned(s, 32),
            "out_si": to_unsigned(si, 32),
            "out_k": to_unsigned(k, 32),
        }


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


def sdiv_trunc_zero(n, d):
    if d == 0:
        return 0
    an = n if n >= 0 else -n
    ad = d if d >= 0 else -d
    q = an // ad
    if (n < 0) ^ (d < 0):
        q = -q
    return q