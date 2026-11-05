def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15CorrelationWindow:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_lag, in_a0, in_a1, in_a2, in_a3, in_b0, in_b1, in_b2):
        a0 = to_signed(in_a0, 16)
        a1 = to_signed(in_a1, 16)
        a2 = to_signed(in_a2, 16)
        a3 = to_signed(in_a3, 16)
        b0 = to_signed(in_b0, 16)
        b1 = to_signed(in_b1, 16)
        b2 = to_signed(in_b2, 16)
        k = in_lag - 1

        if 0 <= k < 6:
            if k == 0:
                acc = a0 * b2
            elif k == 1:
                acc = a0 * b1 + a1 * b2
            elif k == 2:
                acc = a0 * b0 + a1 * b1 + a2 * b2
            elif k == 3:
                acc = a1 * b0 + a2 * b1 + a3 * b2
            elif k == 4:
                acc = a2 * b0 + a3 * b1
            elif k == 5:
                acc = a3 * b0
            else:
                acc = 0
        else:
            acc = 0

        shifted = acc >> 15
        if shifted > 32767:
            out = 32767
        elif shifted < -32768:
            out = -32768
        else:
            out = shifted
        return {"out_corr": to_unsigned(out, 16)}
