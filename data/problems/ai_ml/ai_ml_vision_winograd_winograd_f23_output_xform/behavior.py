def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlVisionWinogradWinogradF23OutputXform:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_m0, in_m1, in_m2, in_m3):
        m0 = to_signed(in_m0, 16)
        m1 = to_signed(in_m1, 16)
        m2 = to_signed(in_m2, 16)
        m3 = to_signed(in_m3, 16)
        y0_full = m0 + m1 + m2
        y1_full = m1 - m2 - m3
        return {
            "out_y0": to_unsigned(y0_full, 18),
            "out_y1": to_unsigned(y1_full, 18),
        }