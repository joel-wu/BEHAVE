def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DilithiumMontgomeryReduce:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        if not (-17996808470921216 < to_signed(in_a, 64) < 17996808470921216):
            return {"out_r":0,"out_error":1}
        q = 8380417
        qinv = 58728449
        a = to_signed(in_a, 64)
        a_reduced = to_unsigned(a, 32)
        a_inverted = to_unsigned(a_reduced * qinv, 32)
        t = to_signed(a_inverted, 32)
        r_pre = a - t * q
        r = r_pre >> 32
        return {"out_error": 0, "out_r": to_unsigned(r, 32)}
