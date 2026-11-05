def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DilithiumReduce32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        if to_signed(in_a, 32) > 2143289343:
            return {"out_r":0,"out_error":1}
        a = to_signed(in_a, 32)
        q = (a + (1 << 22)) >> 23
        r = a - q * 8380417
        return {"out_error": 0, "out_r": to_unsigned(r, 32)}
