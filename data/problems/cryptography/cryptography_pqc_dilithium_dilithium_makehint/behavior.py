def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DilithiumMakeHint:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a0, in_a1):
        a0 = to_signed(in_a0, 24)
        a1 = to_unsigned(in_a1, 6)
        hint = 1 if (a0 > 95232 or a0 < -95232 or (a0 == -95232 and a1 != 0)) else 0
        return {"out_hint": to_unsigned(hint, 1)}