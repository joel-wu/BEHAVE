def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DilithiumCaddq:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        if not (-8380417 < to_signed(in_a, 32) < 8380417):
            return {"out_r":0,"out_error":1}
        q = 8380417
        a = to_signed(in_a, 32)
        r = a if a >= 0 else a + q
        return {"out_error": 0, "out_r": to_unsigned(r, 32)}
