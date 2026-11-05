def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DilithiumUseHint44:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_hint):
        if in_a >= 8380417:
            return {"out_r":0,"out_error":1}
        a = in_a
        t = (a + 127) >> 7
        u = (t * 11275 + (1 << 23)) >> 24
        a1 = 0 if u > 43 else u
        a0_pre = a - a1 * 190464
        a0 = a0_pre - 8380417 if a0_pre > 4190208 else a0_pre

        if in_hint == 0:
            r = a1
        elif a0 > 0:
            r = 0 if a1 == 43 else a1 + 1
        else:
            r = 43 if a1 == 0 else a1 - 1

        return {"out_error": 0, "out_r": to_unsigned(r, 6)}
