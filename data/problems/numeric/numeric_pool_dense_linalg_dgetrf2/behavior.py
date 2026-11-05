def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_zero_div(n, d):
    q = abs(n) // abs(d)
    return -q if (n < 0) ^ (d < 0) else q


class LuFactor2x2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a00, in_a01, in_a10, in_a11):
        a00 = to_signed(in_a00, 32)
        a01 = to_signed(in_a01, 32)
        a10 = to_signed(in_a10, 32)
        a11 = to_signed(in_a11, 32)

        abs_a00 = -a00 if a00 < 0 else a00
        abs_a10 = -a10 if a10 < 0 else a10

        if abs_a00 >= abs_a10:
            out_pivot_row0 = 0
            p00, p01, p10, p11 = a00, a01, a10, a11
        else:
            out_pivot_row0 = 1
            p00, p01, p10, p11 = a10, a11, a00, a01

        if p00 == 0:
            return {
                "out_u00": to_unsigned(p00, 32),
                "out_u01": to_unsigned(p01, 32),
                "out_l10": to_unsigned(p10, 32),
                "out_u11": to_unsigned(p11, 32),
                "out_pivot_row0": to_unsigned(out_pivot_row0, 1),
                "out_info": to_unsigned(1, 2),
            }

        l10 = trunc_zero_div(p10 << 16, p00)
        u11 = p11 - ((l10 * p01) >> 16)
        out_info = 2 if u11 == 0 else 0

        return {
            "out_u00": to_unsigned(p00, 32),
            "out_u01": to_unsigned(p01, 32),
            "out_l10": to_unsigned(l10, 32),
            "out_u11": to_unsigned(u11, 32),
            "out_pivot_row0": to_unsigned(out_pivot_row0, 1),
            "out_info": to_unsigned(out_info, 2),
        }
