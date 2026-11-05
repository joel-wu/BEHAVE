def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DilithiumPower2Round:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        if in_a >= 8380417:
            return {"out_a0":0,"out_a1":0,"out_error":1}
        a1 = (in_a + 4095) >> 13
        a0_signed = in_a - (a1 << 13)
        return {"out_error": 0, "out_a0": to_unsigned(a0_signed, 14), "out_a1": to_unsigned(a1, 10)}
