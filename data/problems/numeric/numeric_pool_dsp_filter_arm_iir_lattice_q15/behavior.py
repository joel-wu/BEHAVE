def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


K = [to_signed(0x2000, 16), to_signed(0xE000, 16), to_signed(0x1000, 16), to_signed(0xF000, 16)]
V = [to_signed(0x1000, 16), to_signed(0x2000, 16), to_signed(0xE000, 16), to_signed(0x0800, 16), to_signed(0xF800, 16)]


class Q15IirLatticeFilter4Stage:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0, 0, 0, 0]

    def process(self, in_sample):
        x = to_signed(in_sample, 16)
        g_prev = [self.state[0], self.state[1], self.state[2], self.state[3]]
        f_curr = x
        acc = 0
        g_new = [0, 0, 0, 0]

        for i in range(4):
            prod_fk = (g_prev[i] * K[i]) >> 15
            f_next = sat16(f_curr - prod_fk)
            prod_gk = (f_next * K[i]) >> 15
            g_next = sat16(prod_gk + g_prev[i])
            acc += g_next * V[i]
            g_new[i] = g_next
            f_curr = f_next

        acc += f_curr * V[4]
        y = sat16(acc >> 15)

        self.state = [g_new[1], g_new[2], g_new[3], f_curr]

        return {"out_sample": to_unsigned(y, 16)}
