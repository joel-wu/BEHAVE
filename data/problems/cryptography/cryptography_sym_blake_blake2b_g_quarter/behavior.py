MASK64 = (1 << 64) - 1


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotr64(x, n):
    x &= MASK64
    return ((x >> n) | ((x << (64 - n)) & MASK64)) & MASK64


class CryptographySymBlakeBlake2BGQuarter:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b, in_c, in_d, in_m0, in_m1):
        a1 = (in_a + in_b + in_m0) & MASK64
        d1 = rotr64(in_d ^ a1, 32)
        c1 = (in_c + d1) & MASK64
        b1 = rotr64(in_b ^ c1, 24)
        a2 = (a1 + b1 + in_m1) & MASK64
        d2 = rotr64(d1 ^ a2, 16)
        c2 = (c1 + d2) & MASK64
        b2 = rotr64(b1 ^ c2, 63)
        return {
            "out_a": to_unsigned(a2, 64),
            "out_b": to_unsigned(b2, 64),
            "out_c": to_unsigned(c2, 64),
            "out_d": to_unsigned(d2, 64),
        }