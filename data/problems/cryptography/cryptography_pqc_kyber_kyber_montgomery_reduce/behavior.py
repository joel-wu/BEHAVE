def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class KyberMontgomeryReduce:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        a = to_signed(in_a, 32)
        a_lo = to_unsigned(a, 16)
        mul_lo = to_unsigned(a_lo * 62209, 16)
        u = to_signed(mul_lo, 16)
        m = a - u * 3329
        t = to_signed(m, 32) >> 16
        return {"out_t": to_unsigned(t, 16)}