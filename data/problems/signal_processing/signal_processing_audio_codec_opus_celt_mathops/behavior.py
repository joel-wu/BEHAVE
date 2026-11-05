def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def to_signed16(v):
    return to_signed(v, 16)


def to_signed32(v):
    return to_signed(v, 32)


def add16(a, b):
    return to_signed16(a + b)


def sub16(a, b):
    return to_signed16(a - b)


def add32(a, b):
    return to_signed32(a + b)


def sub32(a, b):
    return to_signed32(a - b)


def shl16(a, s):
    if s >= 0:
        return to_signed16(a << s)
    return to_signed16(a >> (-s))


def shl32(a, s):
    if s >= 0:
        return to_signed32(a << s)
    return to_signed32(a >> (-s))


def shr32(a, s):
    if s >= 0:
        return to_signed32(a >> s)
    return to_signed32(a << (-s))


def vshr32(a, s):
    if s >= 0:
        return to_signed32(a >> s)
    return to_signed32(a << (-s))


def pshr32(a, s):
    if s > 0:
        return to_signed32((a + (1 << (s - 1))) >> s)
    return to_signed32(a << (-s))


def mult16_16_q15(a, b):
    return to_signed16((a * b) >> 15)


def mult32_32_q31(a, b):
    return to_signed32((a * b) >> 31)


def celt_ilog2_u32(x):
    x &= 0xFFFFFFFF
    if x == 0:
        return 0
    return x.bit_length()


def celt_rsqrt_norm(x):
    n = to_signed16(x - 32768)
    r = add16(23557, mult16_16_q15(n, add16(-13490, mult16_16_q15(n, 6713))))
    r2 = mult16_16_q15(r, r)
    y = shl16(sub16(add16(mult16_16_q15(r2, n), r2), 16384), 1)
    return add16(r, mult16_16_q15(r, mult16_16_q15(y, sub16(mult16_16_q15(y, 12288), 16384))))


def celt_rsqrt_norm32(x):
    x = to_signed32(x)
    if x <= 0:
        return 0
    r_q29 = shl32(celt_rsqrt_norm(shr32(x, 15)), 15)
    tmp = mult32_32_q31(r_q29, r_q29)
    tmp = mult32_32_q31(1073741824, tmp)
    tmp = mult32_32_q31(x, tmp)
    return shl32(mult32_32_q31(r_q29, sub32(201326592, tmp)), 4)


def celt_rcp_norm16(x16):
    r = add16(30840, mult16_16_q15(-15420, x16))
    t = add16(mult16_16_q15(r, x16), add16(r, -32768))
    r = sub16(r, mult16_16_q15(r, t))
    t = add16(mult16_16_q15(r, x16), add16(r, -32768))
    return sub16(r, add16(1, mult16_16_q15(r, t)))


class signal_processing_audio_codec_opus_celt_mathops:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_y = 0

    def process(self, in_op, in_x):
        op = in_op & 0x7
        x_u = in_x & 0xFFFFFFFF
        x_s = to_signed32(in_x)

        if op == 0:
            if x_u == 0:
                y = 0
            else:
                val = x_u
                g = 0
                bshift = (celt_ilog2_u32(val) - 1) >> 1
                b = 1 << bshift
                while bshift >= 0:
                    t = ((g << 1) + b) << bshift
                    if t <= val:
                        g += b
                        val -= t
                    b >>= 1
                    bshift -= 1
                y = g
        elif op == 1:
            y = celt_rsqrt_norm32(x_s)
        elif op == 2:
            if x_s <= 0:
                y = 0
            elif x_s >= 1073741824:
                y = 2147483647
            else:
                k = (celt_ilog2_u32(x_u) - 1) >> 1
                x_frac = vshr32(x_s, 2 * (k - 14) - 1)
                x_frac = mult32_32_q31(celt_rsqrt_norm32(x_frac), x_frac)
                if k < 12:
                    y = pshr32(x_frac, 12 - k)
                else:
                    y = shl32(x_frac, k - 12)
        elif op == 3:
            ax = x_s if x_s >= 0 else -x_s
            if ax == 1073741824:
                y = 0
            else:
                x_sq_q29 = mult32_32_q31(x_s, x_s)
                tmp = add32(-178761936, mult32_32_q31(x_sq_q29, 29487206))
                tmp = add32(544710848, mult32_32_q31(x_sq_q29, tmp))
                tmp = add32(-662336704, mult32_32_q31(x_sq_q29, tmp))
                y = shl32(add32(134217720, mult32_32_q31(x_sq_q29, tmp)), 4)
        elif op == 4:
            if x_s < 1073741824:
                y = 0
            else:
                x16 = to_signed16(shr32(x_s, 15) - 32768)
                r_q30 = shl32(celt_rcp_norm16(x16), 16)
                tmp = mult32_32_q31(r_q30, x_s)
                tmp = add32(tmp, -1073741824)
                tmp = mult32_32_q31(tmp, r_q30)
                y = sub32(r_q30, add32(shl32(tmp, 1), 1))
        elif op == 5:
            y = to_signed32(celt_rcp_norm16(to_signed16(x_u)))
        else:
            y = 0

        self.out_y = to_unsigned(y, 32)
        return {"out_y": self.out_y}
