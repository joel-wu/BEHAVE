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


def sat32(v):
    if v > 2147483647:
        return 2147483647
    if v < -2147483648:
        return -2147483648
    return v


def trunc_div_toward_zero(n, d):
    if d == 0:
        return 0
    an = -n if n < 0 else n
    ad = -d if d < 0 else d
    q = an // ad
    return -q if ((n < 0) != (d < 0)) else q


class control_kalman_ekf_update_step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x0, in_x1,
        in_p00, in_p01, in_p10, in_p11,
        in_h0, in_h1,
        in_hx, in_z,
        in_r
    ):
        x0 = to_signed(in_x0, 16)
        x1 = to_signed(in_x1, 16)
        p00 = to_signed(in_p00, 32)
        p01 = to_signed(in_p01, 32)
        p10 = to_signed(in_p10, 32)
        p11 = to_signed(in_p11, 32)
        h0 = to_signed(in_h0, 16)
        h1 = to_signed(in_h1, 16)
        hx = to_signed(in_hx, 16)
        z = to_signed(in_z, 16)
        r = to_signed(in_r, 32)

        y = sat16(z - hx)

        pht0 = sat32(((p00 * h0) + (p01 * h1)) >> 15)
        pht1 = sat32(((p10 * h0) + (p11 * h1)) >> 15)

        hpht = sat32(((h0 * pht0) + (h1 * pht1)) >> 15)
        s = sat32(hpht + r)

        if s == 0:
            k0 = 0
            k1 = 0
        else:
            k0 = sat16(trunc_div_toward_zero(pht0 << 15, s))
            k1 = sat16(trunc_div_toward_zero(pht1 << 15, s))

        x0_new = sat16(x0 + sat16((k0 * y) >> 15))
        x1_new = sat16(x1 + sat16((k1 * y) >> 15))

        a00 = sat16(32767 - sat16((k0 * h0) >> 15))
        a01 = sat16(0 - sat16((k0 * h1) >> 15))
        a10 = sat16(0 - sat16((k1 * h0) >> 15))
        a11 = sat16(32767 - sat16((k1 * h1) >> 15))

        m00 = sat32(((a00 * p00) + (a01 * p10)) >> 15)
        m01 = sat32(((a00 * p01) + (a01 * p11)) >> 15)
        m10 = sat32(((a10 * p00) + (a11 * p10)) >> 15)
        m11 = sat32(((a10 * p01) + (a11 * p11)) >> 15)

        j00 = sat32(((m00 * a00) + (m01 * a01)) >> 15)
        j01 = sat32(((m00 * a10) + (m01 * a11)) >> 15)
        j10 = sat32(((m10 * a00) + (m11 * a01)) >> 15)
        j11 = sat32(((m10 * a10) + (m11 * a11)) >> 15)

        kr0 = sat32((k0 * r) >> 15)
        kr1 = sat32((k1 * r) >> 15)

        n00 = sat32((kr0 * k0) >> 15)
        n01 = sat32((kr0 * k1) >> 15)
        n10 = sat32((kr1 * k0) >> 15)
        n11 = sat32((kr1 * k1) >> 15)

        p00_new = sat32(j00 + n00)
        p01_new = sat32(j01 + n01)
        p10_new = sat32(j10 + n10)
        p11_new = sat32(j11 + n11)

        return {
            "out_x0": to_unsigned(x0_new, 16),
            "out_x1": to_unsigned(x1_new, 16),
            "out_p00": to_unsigned(p00_new, 32),
            "out_p01": to_unsigned(p01_new, 32),
            "out_p10": to_unsigned(p10_new, 32),
            "out_p11": to_unsigned(p11_new, 32),
            "out_k0": to_unsigned(k0, 16),
            "out_k1": to_unsigned(k1, 16),
            "out_y": to_unsigned(y, 16),
            "out_s": to_unsigned(s, 32),
        }