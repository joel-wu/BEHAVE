def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap16(v):
    return to_signed(v, 16)


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def ashr1(v):
    return to_signed(v, 16) >> 1


def mul16_hi(a, b):
    return wrap16((to_signed(a, 16) * to_signed(b, 16)) >> 16)


class Radix2Q15Butterfly8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_ifft,
        in_x0_re, in_x0_im,
        in_x1_re, in_x1_im,
        in_x2_re, in_x2_im,
        in_x3_re, in_x3_im,
        in_x4_re, in_x4_im,
        in_x5_re, in_x5_im,
        in_x6_re, in_x6_im,
        in_x7_re, in_x7_im,
    ):
        x0r = to_signed(in_x0_re, 16)
        x0i = to_signed(in_x0_im, 16)
        x1r = to_signed(in_x1_re, 16)
        x1i = to_signed(in_x1_im, 16)
        x2r = to_signed(in_x2_re, 16)
        x2i = to_signed(in_x2_im, 16)
        x3r = to_signed(in_x3_re, 16)
        x3i = to_signed(in_x3_im, 16)
        x4r = to_signed(in_x4_re, 16)
        x4i = to_signed(in_x4_im, 16)
        x5r = to_signed(in_x5_re, 16)
        x5i = to_signed(in_x5_im, 16)
        x6r = to_signed(in_x6_re, 16)
        x6i = to_signed(in_x6_im, 16)
        x7r = to_signed(in_x7_re, 16)
        x7i = to_signed(in_x7_im, 16)

        def butterfly_stage12(ar, ai, br, bi, cos, sin, shift_inputs, top_shift):
            if shift_inputs:
                ar = ashr1(ar)
                ai = ashr1(ai)
                br = ashr1(br)
                bi = ashr1(bi)
            xt = wrap16(ar - br)
            yt = wrap16(ai - bi)
            if top_shift:
                top_re = wrap16((ar + br) >> 1)
                top_im = wrap16((ai + bi) >> 1)
            else:
                top_re = wrap16(ar + br)
                top_im = wrap16(ai + bi)
            m0 = mul16_hi(xt, cos)
            m1 = mul16_hi(yt, sin)
            m2 = mul16_hi(yt, cos)
            m3 = mul16_hi(xt, sin)
            if in_ifft:
                low_re = wrap16(m0 - m1)
                low_im = wrap16(m2 + m3)
            else:
                low_re = wrap16(m0 + m1)
                low_im = wrap16(m2 - m3)
            return top_re, top_im, low_re, low_im

        y0r, y0i, y4r, y4i = butterfly_stage12(x0r, x0i, x4r, x4i, 32767, 0, 1, 1)
        y1r, y1i, y5r, y5i = butterfly_stage12(x1r, x1i, x5r, x5i, 23169, 23169, 1, 1)
        y2r, y2i, y6r, y6i = butterfly_stage12(x2r, x2i, x6r, x6i, 0, 32767, 1, 1)
        y3r, y3i, y7r, y7i = butterfly_stage12(x3r, x3i, x7r, x7i, -23169, 23169, 1, 1)

        z0r, z0i, z2r, z2i = butterfly_stage12(y0r, y0i, y2r, y2i, 32767, 0, 0, 1)
        z4r, z4i, z6r, z6i = butterfly_stage12(y4r, y4i, y6r, y6i, 32767, 0, 0, 1)
        z1r, z1i, z3r, z3i = butterfly_stage12(y1r, y1i, y3r, y3i, 0, 32767, 0, 1)
        z5r, z5i, z7r, z7i = butterfly_stage12(y5r, y5i, y7r, y7i, 0, 32767, 0, 1)

        w0r = sat16(z0r + z1r)
        w0i = sat16(z0i + z1i)
        w1r = sat16(z0r - z1r)
        w1i = sat16(z0i - z1i)

        w2r = sat16(z2r + z3r)
        w2i = sat16(z2i + z3i)
        w3r = sat16(z2r - z3r)
        w3i = sat16(z2i - z3i)

        w4r = sat16(z4r + z5r)
        w4i = sat16(z4i + z5i)
        w5r = sat16(z4r - z5r)
        w5i = sat16(z4i - z5i)

        w6r = sat16(z6r + z7r)
        w6i = sat16(z6i + z7i)
        w7r = sat16(z6r - z7r)
        w7i = sat16(z6i - z7i)

        return {
            "out_x0_re": to_unsigned(w0r, 16),
            "out_x0_im": to_unsigned(w0i, 16),
            "out_x1_re": to_unsigned(w4r, 16),
            "out_x1_im": to_unsigned(w4i, 16),
            "out_x2_re": to_unsigned(w2r, 16),
            "out_x2_im": to_unsigned(w2i, 16),
            "out_x3_re": to_unsigned(w6r, 16),
            "out_x3_im": to_unsigned(w6i, 16),
            "out_x4_re": to_unsigned(w1r, 16),
            "out_x4_im": to_unsigned(w1i, 16),
            "out_x5_re": to_unsigned(w5r, 16),
            "out_x5_im": to_unsigned(w5i, 16),
            "out_x6_re": to_unsigned(w3r, 16),
            "out_x6_im": to_unsigned(w3i, 16),
            "out_x7_re": to_unsigned(w7r, 16),
            "out_x7_im": to_unsigned(w7i, 16),
        }