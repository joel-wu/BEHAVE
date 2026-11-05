def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def ordered_less(a, b):
    ma, mb = a & 0x7FFFFFFF, b & 0x7FFFFFFF
    if ma > 0x7F800000 or mb > 0x7F800000:
        return False
    if ma == 0 and mb == 0:
        return False
    sa, sb = (a >> 31) & 1, (b >> 31) & 1
    if sa != sb:
        return sa == 1
    return ma > mb if sa else ma < mb


class Fp32Clip4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_low, in_high):
        def clip(x, low, high):
            if ordered_less(high, x):
                return to_unsigned(high, 32)
            if ordered_less(x, low):
                return to_unsigned(low, 32)
            return to_unsigned(x, 32)

        return {
            "out_y0": clip(in_x0, in_low, in_high),
            "out_y1": clip(in_x1, in_low, in_high),
            "out_y2": clip(in_x2, in_low, in_high),
            "out_y3": clip(in_x3, in_low, in_high),
        }
