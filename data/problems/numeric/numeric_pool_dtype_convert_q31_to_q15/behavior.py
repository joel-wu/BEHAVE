def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q31ToQ15Converter:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_sample):
        x = to_signed(in_sample, 32)
        y = x >> 16
        return {"out_sample": to_unsigned(y, 16)}