def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


Q = 8380417
QINV = 58728449


class CryptographyPqcDilithiumDilithiumNttButterfly:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def montgomery_reduce(self, x):
        x64 = to_unsigned(x, 64)
        u = to_unsigned(x64 * QINV, 32)
        u_s = to_signed(u, 32)
        y = x - u_s * Q
        t = y >> 32
        return to_signed(t, 32)

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_zeta
    ):
        a0 = to_signed(in_a0, 32)
        a1 = to_signed(in_a1, 32)
        a2 = to_signed(in_a2, 32)
        a3 = to_signed(in_a3, 32)
        a4 = to_signed(in_a4, 32)
        a5 = to_signed(in_a5, 32)
        a6 = to_signed(in_a6, 32)
        a7 = to_signed(in_a7, 32)

        b0 = to_signed(in_b0, 32)
        b1 = to_signed(in_b1, 32)
        b2 = to_signed(in_b2, 32)
        b3 = to_signed(in_b3, 32)
        b4 = to_signed(in_b4, 32)
        b5 = to_signed(in_b5, 32)
        b6 = to_signed(in_b6, 32)
        b7 = to_signed(in_b7, 32)

        zeta = to_signed(in_zeta, 32)

        t0 = self.montgomery_reduce(b0 * zeta)
        t1 = self.montgomery_reduce(b1 * zeta)
        t2 = self.montgomery_reduce(b2 * zeta)
        t3 = self.montgomery_reduce(b3 * zeta)
        t4 = self.montgomery_reduce(b4 * zeta)
        t5 = self.montgomery_reduce(b5 * zeta)
        t6 = self.montgomery_reduce(b6 * zeta)
        t7 = self.montgomery_reduce(b7 * zeta)

        return {
            "out_a0": to_unsigned(a0 + t0, 32),
            "out_a1": to_unsigned(a1 + t1, 32),
            "out_a2": to_unsigned(a2 + t2, 32),
            "out_a3": to_unsigned(a3 + t3, 32),
            "out_a4": to_unsigned(a4 + t4, 32),
            "out_a5": to_unsigned(a5 + t5, 32),
            "out_a6": to_unsigned(a6 + t6, 32),
            "out_a7": to_unsigned(a7 + t7, 32),
            "out_b0": to_unsigned(a0 - t0, 32),
            "out_b1": to_unsigned(a1 - t1, 32),
            "out_b2": to_unsigned(a2 - t2, 32),
            "out_b3": to_unsigned(a3 - t3, 32),
            "out_b4": to_unsigned(a4 - t4, 32),
            "out_b5": to_unsigned(a5 - t5, 32),
            "out_b6": to_unsigned(a6 - t6, 32),
            "out_b7": to_unsigned(a7 - t7, 32),
        }