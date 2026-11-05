class BiquadIirTdf2Q15:
    def __init__(self):
        self.reset()

    def reset(self):
        self.z1 = 0
        self.z2 = 0

    def process(self, in_x, in_a0, in_a1, in_a2, in_b1, in_b2):
        def to_unsigned(v, b):
            return v & ((1 << b) - 1)

        def to_signed(v, b):
            v &= (1 << b) - 1
            if v & (1 << (b - 1)):
                v -= 1 << b
            return v

        def sat_q15(v):
            if v > 32767:
                return 32767
            if v < -32768:
                return -32768
            return v

        def q15_mul(a, b):
            return sat_q15((a * b) >> 15)

        x = to_signed(in_x, 16)
        a0 = to_signed(in_a0, 16)
        a1 = to_signed(in_a1, 16)
        a2 = to_signed(in_a2, 16)
        b1 = to_signed(in_b1, 16)
        b2 = to_signed(in_b2, 16)
        z1_old = self.z1
        z2_old = self.z2

        p0 = q15_mul(x, a0)
        out_y_signed = sat_q15(p0 + z1_old)

        p1 = q15_mul(x, a1)
        p2 = q15_mul(x, a2)
        f1 = q15_mul(b1, out_y_signed)
        f2 = q15_mul(b2, out_y_signed)

        self.z1 = sat_q15(p1 + z2_old - f1)
        self.z2 = sat_q15(p2 - f2)

        return {"out_y": to_unsigned(out_y_signed, 16)}
