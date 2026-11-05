def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CryptographyPqcKyberKyberBarrettReduce:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        q = 3329
        v = 20159
        a = to_signed(in_a, 16)
        p = v * a
        p_round = p + (1 << 25)
        t = p_round >> 26
        tq = t * q
        r = a - tq
        return {"out_r": to_unsigned(r, 16)}