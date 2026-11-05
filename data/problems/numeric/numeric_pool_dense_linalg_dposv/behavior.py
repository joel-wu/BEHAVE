def signed64(x):
    x &= (1 << 64) - 1
    return x - (1 << 64) if x >= (1 << 63) else x

def qmul(x, y):
    return signed64((x * y) >> 32)

def qdiv(x, y):
    if y == 0:
        return 0
    n = x << 32
    q = abs(n) // abs(y)
    return signed64(-q if (n < 0) != (y < 0) else q)

def sqrt_q32(x):
    root = 0
    for bit in range(47, -1, -1):
        trial = root | (1 << bit)
        if trial * trial <= x << 32:
            root = trial
    return root

def triangular(a, b, upper, unit):
    x = [0] * 4
    for step in range(4):
        i = 3 - step if upper else step
        acc = b[i]
        for k in range(4):
            if (upper and k > i) or (not upper and k < i):
                acc = signed64(acc - qmul(a[i][k], x[k]))
        x[i] = acc if unit else qdiv(acc, a[i][i])
    return x

class CholeskySolve2x2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a00, in_a10, in_a11, in_b0, in_b1):
        a00 = signed64(in_a00)
        a10 = signed64(in_a10)
        a11 = signed64(in_a11)
        b0 = signed64(in_b0)
        b1 = signed64(in_b1)
        info = 0
        l00 = 0
        l10 = 0
        l11 = 0
        x0 = 0
        x1 = 0
        if a00 <= 0:
            info = 1
        else:
            l00 = sqrt_q32(a00)
            l10 = qdiv(a10, l00)
            pivot = signed64(a11 - qmul(l10, l10))
            if pivot <= 0:
                info = 2
            else:
                l11 = sqrt_q32(pivot)
                y0 = qdiv(b0, l00)
                y1 = qdiv(signed64(b1 - qmul(l10, y0)), l11)
                x1 = qdiv(y1, l11)
                x0 = qdiv(signed64(y0 - qmul(l10, x1)), l00)

        return {
            "out_info": (info) & ((1 << 32) - 1),
            "out_x0": (x0) & ((1 << 64) - 1),
            "out_x1": (x1) & ((1 << 64) - 1),
            "out_l00": (l00) & ((1 << 64) - 1),
            "out_l10": (l10) & ((1 << 64) - 1),
            "out_l11": (l11) & ((1 << 64) - 1),
        }
