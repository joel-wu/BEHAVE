def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(x):
    if x > 32767:
        return 32767
    if x < -32768:
        return -32768
    return x


def mul_q15(a, b):
    return (a * b) >> 15


class Q15FirLattice4Stage:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0, 0, 0, 0]
        self.coeff = [0, 0, 0, 0]

    def process(self, in_x):
        f0 = to_signed(in_x, 16)
        g0_prev = self.state[0]
        g1_prev = self.state[1]
        g2_prev = self.state[2]
        g3_prev = self.state[3]
        k1 = to_signed(self.coeff[0], 16)
        k2 = to_signed(self.coeff[1], 16)
        k3 = to_signed(self.coeff[2], 16)
        k4 = to_signed(self.coeff[3], 16)

        f1 = sat16(f0 + mul_q15(g0_prev, k1))
        g1 = sat16(g0_prev + mul_q15(f0, k1))

        f2 = sat16(f1 + mul_q15(g1_prev, k2))
        g2 = sat16(g1_prev + mul_q15(f1, k2))

        f3 = sat16(f2 + mul_q15(g2_prev, k3))
        g3 = sat16(g2_prev + mul_q15(f2, k3))

        f4 = sat16(f3 + mul_q15(g3_prev, k4))
        _g4 = sat16(g3_prev + mul_q15(f3, k4))

        self.state = [f0, g1, g2, g3]

        return {"out_y": to_unsigned(f4, 16)}