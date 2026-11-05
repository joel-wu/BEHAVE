def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotl32(v, s):
    v = to_unsigned(v, 32)
    return to_unsigned((v << s) | (v >> (32 - s)), 32)


def quarterround(a, b, c, d):
    b = to_unsigned(b ^ rotl32(to_unsigned(a + d, 32), 7), 32)
    c = to_unsigned(c ^ rotl32(to_unsigned(b + a, 32), 9), 32)
    d = to_unsigned(d ^ rotl32(to_unsigned(c + b, 32), 13), 32)
    a = to_unsigned(a ^ rotl32(to_unsigned(d + c, 32), 18), 32)
    return a, b, c, d


class CryptographySymNonceExtendXsalsa20HsalsaExtend:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_input, in_key, in_const):
        x0 = to_unsigned(in_const >> 0, 32)
        x5 = to_unsigned(in_const >> 32, 32)
        x10 = to_unsigned(in_const >> 64, 32)
        x15 = to_unsigned(in_const >> 96, 32)

        x1 = to_unsigned(in_key >> 0, 32)
        x2 = to_unsigned(in_key >> 32, 32)
        x3 = to_unsigned(in_key >> 64, 32)
        x4 = to_unsigned(in_key >> 96, 32)
        x11 = to_unsigned(in_key >> 128, 32)
        x12 = to_unsigned(in_key >> 160, 32)
        x13 = to_unsigned(in_key >> 192, 32)
        x14 = to_unsigned(in_key >> 224, 32)

        x6 = to_unsigned(in_input >> 0, 32)
        x7 = to_unsigned(in_input >> 32, 32)
        x8 = to_unsigned(in_input >> 64, 32)
        x9 = to_unsigned(in_input >> 96, 32)

        for _ in range(10):
            x0, x4, x8, x12 = quarterround(x0, x4, x8, x12)
            x5, x9, x13, x1 = quarterround(x5, x9, x13, x1)
            x10, x14, x2, x6 = quarterround(x10, x14, x2, x6)
            x15, x3, x7, x11 = quarterround(x15, x3, x7, x11)

            x0, x1, x2, x3 = quarterround(x0, x1, x2, x3)
            x5, x6, x7, x4 = quarterround(x5, x6, x7, x4)
            x10, x11, x8, x9 = quarterround(x10, x11, x8, x9)
            x15, x12, x13, x14 = quarterround(x15, x12, x13, x14)

        out_subkey = (
            (to_unsigned(x0, 32) << 0)
            | (to_unsigned(x5, 32) << 32)
            | (to_unsigned(x10, 32) << 64)
            | (to_unsigned(x15, 32) << 96)
            | (to_unsigned(x6, 32) << 128)
            | (to_unsigned(x7, 32) << 160)
            | (to_unsigned(x8, 32) << 192)
            | (to_unsigned(x9, 32) << 224)
        )
        return {"out_subkey": to_unsigned(out_subkey, 256)}