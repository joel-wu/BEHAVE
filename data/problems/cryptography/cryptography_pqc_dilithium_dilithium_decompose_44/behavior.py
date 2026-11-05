def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DilithiumDecompose44:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        if in_a >= 8380417:
            return {"out_a0":0,"out_a1":0,"out_error":1}
        t = (in_a + 127) >> 7
        u = (t * 11275 + 8388608) >> 24
        a1 = 0 if u == 44 else u
        a0_pre = in_a - a1 * 190464
        a0 = a0_pre - 8380417 if a0_pre > 4190208 else a0_pre
        return {"out_error": 0, "out_a0": to_unsigned(a0, 24), "out_a1": to_unsigned(a1, 6)}
