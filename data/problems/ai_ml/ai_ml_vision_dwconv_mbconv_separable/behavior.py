def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero(n, d):
    if d == 0:
        return 0
    q = abs(n) // abs(d)
    return q if (n >= 0) == (d >= 0) else -q


def unpack9_signed8(word):
    return [to_signed((word >> (8 * k)) & 0xFF, 8) for k in range(9)]


def unpack4_signed8(word):
    return [to_signed((word >> (8 * k)) & 0xFF, 8) for k in range(4)]


class DwConvMbConvSeparable:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_patch0,
        in_patch1,
        in_patch2,
        in_patch3,
        in_dw_w0,
        in_dw_w1,
        in_dw_w2,
        in_dw_w3,
        in_se0,
        in_se1,
        in_se2,
        in_se3,
        in_pw_row0,
        in_pw_row1,
        in_pw_row2,
        in_pw_row3,
        in_use_se,
    ):
        patches = [
            unpack9_signed8(in_patch0),
            unpack9_signed8(in_patch1),
            unpack9_signed8(in_patch2),
            unpack9_signed8(in_patch3),
        ]
        dw_ws = [
            unpack9_signed8(in_dw_w0),
            unpack9_signed8(in_dw_w1),
            unpack9_signed8(in_dw_w2),
            unpack9_signed8(in_dw_w3),
        ]
        dw_sum = [
            sum(patches[c][k] * dw_ws[c][k] for k in range(9))
            for c in range(4)
        ]
        se = [in_se0 & 0xFF, in_se1 & 0xFF, in_se2 & 0xFF, in_se3 & 0xFF]
        if in_use_se & 1:
            scaled = [trunc_div_toward_zero(dw_sum[c] * se[c], 256) for c in range(4)]
        else:
            scaled = dw_sum
        pw = [
            unpack4_signed8(in_pw_row0),
            unpack4_signed8(in_pw_row1),
            unpack4_signed8(in_pw_row2),
            unpack4_signed8(in_pw_row3),
        ]
        out = [
            sum(scaled[k] * pw[r][k] for k in range(4))
            for r in range(4)
        ]
        return {
            "out_y0": to_unsigned(out[0], 16),
            "out_y1": to_unsigned(out[1], 16),
            "out_y2": to_unsigned(out[2], 16),
            "out_y3": to_unsigned(out[3], 16),
        }