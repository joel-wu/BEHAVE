def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotl32(x, n):
    x = to_unsigned(x, 32)
    return to_unsigned((x << n) | (x >> (32 - n)), 32)


class ChaCha20QuarterRound:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b, in_c, in_d):
        a1 = to_unsigned(in_a + in_b, 32)
        d1 = to_unsigned(in_d ^ a1, 32)
        d2 = rotl32(d1, 16)
        c1 = to_unsigned(in_c + d2, 32)
        b1 = to_unsigned(in_b ^ c1, 32)
        b2 = rotl32(b1, 12)
        a2 = to_unsigned(a1 + b2, 32)
        d3 = to_unsigned(d2 ^ a2, 32)
        d4 = rotl32(d3, 8)
        c2 = to_unsigned(c1 + d4, 32)
        b3 = to_unsigned(b2 ^ c2, 32)
        b4 = rotl32(b3, 7)
        return {
            "out_a": to_unsigned(a2, 32),
            "out_b": to_unsigned(b4, 32),
            "out_c": to_unsigned(c2, 32),
            "out_d": to_unsigned(d4, 32),
        }