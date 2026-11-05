def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15ToFloat4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        return {
            "out_y0": self._q15_to_fp32(in_x0),
            "out_y1": self._q15_to_fp32(in_x1),
            "out_y2": self._q15_to_fp32(in_x2),
            "out_y3": self._q15_to_fp32(in_x3),
        }

    def _q15_to_fp32(self, x):
        s = to_signed(x, 16)
        if s == 0:
            return 0
        sign = 0x80000000 if s < 0 else 0
        mag = -s if s < 0 else s
        msb = 0
        for i in range(15, -1, -1):
            if (mag >> i) & 1:
                msb = i
                break
        exponent = msb + 112
        if msb <= 15:
            frac = (mag << (23 - msb)) & 0x7FFFFF
        else:
            frac = (mag >> (msb - 23)) & 0x7FFFFF
        return to_unsigned(sign | (exponent << 23) | frac, 32)