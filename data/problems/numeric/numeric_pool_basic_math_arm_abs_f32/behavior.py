def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class VectorAbsF32x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a0, in_a1, in_a2, in_a3):
        return {
            "out_r0": to_unsigned(in_a0 & 0x7FFFFFFF, 32),
            "out_r1": to_unsigned(in_a1 & 0x7FFFFFFF, 32),
            "out_r2": to_unsigned(in_a2 & 0x7FFFFFFF, 32),
            "out_r3": to_unsigned(in_a3 & 0x7FFFFFFF, 32),
        }