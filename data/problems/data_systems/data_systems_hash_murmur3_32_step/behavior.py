def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DataSystemsHashMurmur332Step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_h, in_k):
        c1 = 0xCC9E2D51
        c2 = 0x1B873593
        c3 = 0x00000005
        c4 = 0xE6546B64
        k1 = to_unsigned(in_k * c1, 32)
        k2 = to_unsigned((k1 << 15) | (k1 >> 17), 32)
        k3 = to_unsigned(k2 * c2, 32)
        h1 = to_unsigned(in_h ^ k3, 32)
        h2 = to_unsigned((h1 << 13) | (h1 >> 19), 32)
        h3 = to_unsigned(h2 * c3 + c4, 32)
        return {"out_h": to_unsigned(h3, 32)}