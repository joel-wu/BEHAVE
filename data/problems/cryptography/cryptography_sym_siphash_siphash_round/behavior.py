MASK64 = (1 << 64) - 1


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotl64(x, n):
    x &= MASK64
    return ((x << n) | (x >> (64 - n))) & MASK64


class SipHashRound:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_v0, in_v1, in_v2, in_v3):
        v0 = to_unsigned(in_v0, 64)
        v1 = to_unsigned(in_v1, 64)
        v2 = to_unsigned(in_v2, 64)
        v3 = to_unsigned(in_v3, 64)

        v0 = to_unsigned(v0 + v1, 64)
        v1 = rotl64(v1, 13)
        v1 = to_unsigned(v1 ^ v0, 64)
        v0 = rotl64(v0, 32)
        v2 = to_unsigned(v2 + v3, 64)
        v3 = rotl64(v3, 16)
        v3 = to_unsigned(v3 ^ v2, 64)
        v0 = to_unsigned(v0 + v3, 64)
        v3 = rotl64(v3, 21)
        v3 = to_unsigned(v3 ^ v0, 64)
        v2 = to_unsigned(v2 + v1, 64)
        v1 = rotl64(v1, 17)
        v1 = to_unsigned(v1 ^ v2, 64)
        v2 = rotl64(v2, 32)

        return {
            "out_v0": to_unsigned(v0, 64),
            "out_v1": to_unsigned(v1, 64),
            "out_v2": to_unsigned(v2, 64),
            "out_v3": to_unsigned(v3, 64),
        }