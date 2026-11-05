def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CryptographyPqcKyberKyberNttButterfly:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def montgomery_reduce(self, x):
        m = to_signed(x * 62209, 16)
        t = x - m * 3329
        return t >> 16

    def fqmul(self, a, b):
        return self.montgomery_reduce(a * b)

    def process(self, in_a0, in_a1, in_b0, in_b1, in_zeta):
        if (
            not (-3329 < to_signed(in_a0, 16) < 3329)
            or not (-3329 < to_signed(in_a1, 16) < 3329)
            or not (-3329 < to_signed(in_b0, 16) < 3329)
            or not (-3329 < to_signed(in_b1, 16) < 3329)
            or not (-3329 < to_signed(in_zeta, 16) < 3329)
        ):
            return {"out_r0":0,"out_r1":0,"out_error":1}
        a0 = to_signed(in_a0, 16)
        a1 = to_signed(in_a1, 16)
        b0 = to_signed(in_b0, 16)
        b1 = to_signed(in_b1, 16)
        zeta = to_signed(in_zeta, 16)

        p0 = self.fqmul(a1, b1)
        p1 = self.fqmul(p0, zeta)
        p2 = self.fqmul(a0, b0)
        r0 = p1 + p2
        p3 = self.fqmul(a0, b1)
        p4 = self.fqmul(a1, b0)
        r1 = p3 + p4

        return {"out_error": 0, "out_r0": to_unsigned(r0, 16), "out_r1": to_unsigned(r1, 16)}
