def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15VectorAdd4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a0, in_a1, in_a2, in_a3, in_b0, in_b1, in_b2, in_b3):
        a0 = to_signed(in_a0, 16)
        a1 = to_signed(in_a1, 16)
        a2 = to_signed(in_a2, 16)
        a3 = to_signed(in_a3, 16)
        b0 = to_signed(in_b0, 16)
        b1 = to_signed(in_b1, 16)
        b2 = to_signed(in_b2, 16)
        b3 = to_signed(in_b3, 16)

        s0 = a0 + b0
        s1 = a1 + b1
        s2 = a2 + b2
        s3 = a3 + b3

        c0 = 32767 if s0 > 32767 else (-32768 if s0 < -32768 else s0)
        c1 = 32767 if s1 > 32767 else (-32768 if s1 < -32768 else s1)
        c2 = 32767 if s2 > 32767 else (-32768 if s2 < -32768 else s2)
        c3 = 32767 if s3 > 32767 else (-32768 if s3 < -32768 else s3)

        return {
            "out_c0": to_unsigned(c0, 16),
            "out_c1": to_unsigned(c1, 16),
            "out_c2": to_unsigned(c2, 16),
            "out_c3": to_unsigned(c3, 16),
        }