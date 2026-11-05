def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def mul_q16(x, y):
    return to_signed((to_signed(x, 32) * to_signed(y, 32)) >> 16, 32)


class HouseholderQColumnStep:
    def __init__(self):
        self.reset()

    def reset(self):
        self.a = [0] * 64
        self.tau = [0] * 8

    def process(self, in_m, in_n, in_i):
        m = in_m
        n = in_n
        i = in_i

        valid = (m >= 1) and (m <= 8) and (n >= 1) and (n <= m) and (i < n)

        if valid:
            old_a = self.a[:]
            tau_i = to_signed(self.tau[i], 32)

            for c in range(8):
                if i < c < n:
                    dot = 0
                    for r in range(8):
                        if i <= r < m:
                            v_elem = 65536 if r == i else old_a[r * 8 + i]
                            a_elem = old_a[r * 8 + c]
                            dot = to_signed(dot + mul_q16(v_elem, a_elem), 32)
                    w = mul_q16(tau_i, dot)
                    for r in range(8):
                        if i <= r < m:
                            addr = r * 8 + c
                            v_elem = 65536 if r == i else old_a[r * 8 + i]
                            updated = old_a[addr] - mul_q16(v_elem, w)
                            self.a[addr] = to_unsigned(updated, 32)

            for r in range(8):
                if i < r < m:
                    addr = r * 8 + i
                    self.a[addr] = to_unsigned(mul_q16(old_a[addr], to_signed(-tau_i, 32)), 32)

            self.a[i * 8 + i] = to_unsigned(to_signed(0x00010000 - tau_i, 32), 32)

            for r in range(8):
                if r < i:
                    self.a[r * 8 + i] = 0

        return {"out_done": 1}
