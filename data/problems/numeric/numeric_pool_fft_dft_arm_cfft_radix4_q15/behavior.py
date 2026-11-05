def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(v):
    return min(32767, max(-32768, v))


class Radix4Q15ButterflyKernel:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_ifft,
        in_xa_re,
        in_xa_im,
        in_xb_re,
        in_xb_im,
        in_xc_re,
        in_xc_im,
        in_xd_re,
        in_xd_im,
    ):
        xa = to_signed(in_xa_re, 16)
        ya = to_signed(in_xa_im, 16)
        xb = to_signed(in_xb_re, 16)
        yb = to_signed(in_xb_im, 16)
        xc = to_signed(in_xc_re, 16)
        yc = to_signed(in_xc_im, 16)
        xd = to_signed(in_xd_re, 16)
        yd = to_signed(in_xd_im, 16)

        r0_re = sat16(xa + xc)
        r0_im = sat16(ya + yc)
        s0_re = sat16(xa - xc)
        s0_im = sat16(ya - yc)
        t0_re = sat16(xb + xd)
        t0_im = sat16(yb + yd)
        u0_re = sat16(xb - xd)
        u0_im = sat16(yb - yd)

        x0_re = (r0_re >> 1) + (t0_re >> 1)
        x0_im = (r0_im >> 1) + (t0_im >> 1)
        x2_re = (r0_re >> 1) - (t0_re >> 1)
        x2_im = (r0_im >> 1) - (t0_im >> 1)

        if in_ifft:
            x1_re = (s0_re >> 1) - (u0_im >> 1)
            x1_im = (s0_im >> 1) + (u0_re >> 1)
            x3_re = (s0_re >> 1) + (u0_im >> 1)
            x3_im = (s0_im >> 1) - (u0_re >> 1)
        else:
            x1_re = (s0_re >> 1) + (u0_im >> 1)
            x1_im = (s0_im >> 1) - (u0_re >> 1)
            x3_re = (s0_re >> 1) - (u0_im >> 1)
            x3_im = (s0_im >> 1) + (u0_re >> 1)

        return {
            "out_x0_re": to_unsigned(x0_re, 16),
            "out_x0_im": to_unsigned(x0_im, 16),
            "out_x1_re": to_unsigned(x1_re, 16),
            "out_x1_im": to_unsigned(x1_im, 16),
            "out_x2_re": to_unsigned(x2_re, 16),
            "out_x2_im": to_unsigned(x2_im, 16),
            "out_x3_re": to_unsigned(x3_re, 16),
            "out_x3_im": to_unsigned(x3_im, 16),
        }
