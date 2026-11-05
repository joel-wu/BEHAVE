def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15VectorOffset:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_sample, in_offset):
        sample = to_signed(in_sample, 16)
        offset = to_signed(in_offset, 16)
        sum17 = sample + offset
        if sum17 < -32768:
            result = -32768
        elif sum17 > 32767:
            result = 32767
        else:
            result = sum17
        return {"out_sample": to_unsigned(result, 16)}