def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class HouseholderQR2x2Step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def trunc_q16_mul(self, a, b):
        return (a * b) >> 16

    def trunc_q16_div(self, a, b):
        if b == 0:
            return 0
        n = a << 16
        an = -n if n < 0 else n
        ab = -b if b < 0 else b
        q = an // ab
        return -q if (n < 0) ^ (b < 0) else q

    def norm_q16_2(self, x, y):
        target = x * x + y * y
        r = 0
        for i in range(31, -1, -1):
            t = r | (1 << i)
            if t * t <= target:
                r = t
        return r

    def process(self, in_a00, in_a01, in_a10, in_a11):
        x0 = to_signed(in_a00, 32)
        x1 = to_signed(in_a01, 32)
        y0 = to_signed(in_a10, 32)
        y1 = to_signed(in_a11, 32)

        norm0 = self.norm_q16_2(x0, y0)

        if norm0 == 0:
            beta0 = 0
        elif x0 < 0:
            beta0 = norm0
        else:
            beta0 = -norm0

        if y0 == 0:
            tau0 = 0
            v10 = 0
            r00 = x0
        else:
            u0 = x0 - beta0
            v10 = self.trunc_q16_div(y0, u0)
            tau0 = self.trunc_q16_div(beta0 - x0, beta0)
            r00 = beta0

        if tau0 == 0:
            r01 = x1
            c11 = y1
        else:
            dot0 = x1 + self.trunc_q16_mul(v10, y1)
            w0 = self.trunc_q16_mul(tau0, dot0)
            r01 = x1 - w0
            c11 = y1 - self.trunc_q16_mul(v10, w0)

        r11 = c11
        tau1 = 0

        return {
            "out_a00": to_unsigned(r00, 32),
            "out_a01": to_unsigned(r01, 32),
            "out_a10": to_unsigned(v10, 32),
            "out_a11": to_unsigned(r11, 32),
            "out_tau0": to_unsigned(tau0, 32),
            "out_tau1": to_unsigned(tau1, 32),
        }
