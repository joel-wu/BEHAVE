def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15AbsStream:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_sample = 0

    def process(self, in_sample):
        x = to_signed(in_sample, 16)
        if x > 0:
            y = x
        elif x == 0:
            y = 0
        elif x == -32768:
            y = 32767
        else:
            y = -x
        self.out_sample = to_unsigned(y, 16)
        return {"out_sample": self.out_sample}