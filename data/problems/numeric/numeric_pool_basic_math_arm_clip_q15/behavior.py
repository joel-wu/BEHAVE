def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15ClipElement:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_sample = 0

    def process(self, in_sample, in_low, in_high):
        sample = to_signed(in_sample, 16)
        low = to_signed(in_low, 16)
        high = to_signed(in_high, 16)
        if sample > high:
            result = high
        elif sample < low:
            result = low
        else:
            result = sample
        self.out_sample = to_unsigned(result, 16)
        return {"out_sample": self.out_sample}