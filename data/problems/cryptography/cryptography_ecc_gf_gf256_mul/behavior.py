def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class GF256Multiplier:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b):
        acc = 0
        a = to_unsigned(in_a, 8)
        b = to_unsigned(in_b, 8)
        for _ in range(8):
            if b & 1:
                acc ^= a
            a_shift = to_unsigned(a << 1, 8)
            if a & 0x80:
                a = a_shift ^ 0x1B
            else:
                a = a_shift
            b >>= 1
        return {"out_p": to_unsigned(acc, 8)}