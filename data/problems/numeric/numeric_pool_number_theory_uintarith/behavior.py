def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def udivrem_64_by_64(n, d):
    q = 0
    r = 0
    for i in range(63, -1, -1):
        r = (r << 1) | ((n >> i) & 1)
        if r >= d:
            r = r - d
            q |= 1 << i
    return q, r


def udivrem_128_by_64(n_hi, n_lo, d):
    q = 0
    r = 0
    for i in range(127, -1, -1):
        bit = (n_hi >> (i - 64)) & 1 if i >= 64 else (n_lo >> i) & 1
        r = (r << 1) | bit
        if r >= d:
            r = r - d
            if i < 64:
                q |= 1 << i
    return q, r


class UInt192ByUInt64Divider:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_quotient_0 = 0
        self.out_quotient_1 = 0
        self.out_quotient_2 = 0
        self.out_remainder = 0

    def process(self, in_numerator_0, in_numerator_1, in_numerator_2, in_denominator):
        u0 = to_unsigned(in_numerator_0, 64)
        u1 = to_unsigned(in_numerator_1, 64)
        u2 = to_unsigned(in_numerator_2, 64)
        d = to_unsigned(in_denominator, 64)

        if d == 0:
            q2 = 0
            q1 = 0
            q0 = 0
            r0 = 0
        else:
            q2, r2 = udivrem_64_by_64(u2, d)
            q1, r1 = udivrem_128_by_64(r2, u1, d)
            q0, r0 = udivrem_128_by_64(r1, u0, d)

        self.out_quotient_0 = to_unsigned(q0, 64)
        self.out_quotient_1 = to_unsigned(q1, 64)
        self.out_quotient_2 = to_unsigned(q2, 64)
        self.out_remainder = to_unsigned(r0, 64)

        return {
            "out_quotient_0": self.out_quotient_0,
            "out_quotient_1": self.out_quotient_1,
            "out_quotient_2": self.out_quotient_2,
            "out_remainder": self.out_remainder,
        }