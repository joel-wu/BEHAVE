def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15VectorShift4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def _shift_lane(self, x_bits, s_bits):
        x = to_signed(x_bits, 16)
        s = to_signed(s_bits, 8)
        if s >= 0:
            v = x * (1 << s)
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
        else:
            r = -s
            if r >= 16:
                v = -1 if x < 0 else 0
            else:
                v = x >> r
        return to_unsigned(v, 16)

    def process(self, in_x0, in_x1, in_x2, in_x3, in_shiftBits):
        return {
            "out_y0": self._shift_lane(in_x0, in_shiftBits),
            "out_y1": self._shift_lane(in_x1, in_shiftBits),
            "out_y2": self._shift_lane(in_x2, in_shiftBits),
            "out_y3": self._shift_lane(in_x3, in_shiftBits),
        }