def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15ToQ31Converter:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_sample):
        s = to_signed(in_sample, 16)
        out_sample = to_unsigned(s << 16, 32)
        return {"out_sample": out_sample}