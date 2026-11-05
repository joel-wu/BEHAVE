def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SemiInterp1DFineKernel:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_p0, in_p1, in_e_left, in_e_right):
        p0 = to_signed(in_p0, 32)
        p1 = to_signed(in_p1, 32)
        e_left = to_signed(in_e_left, 32)
        e_right = to_signed(in_e_right, 32)
        sum64 = (p0 * e_left) + (p1 * e_right)
        return {"out_e": to_unsigned(sum64, 32)}