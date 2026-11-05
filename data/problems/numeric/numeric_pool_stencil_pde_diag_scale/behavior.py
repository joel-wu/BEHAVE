def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero(a, b):
    if b == 0:
        return 0
    aa = a if a >= 0 else -a
    bb = b if b >= 0 else -b
    q = aa // bb
    if (a < 0) ^ (b < 0):
        q = -q
    return q


class DiagScaleElementwiseDivider:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_x = 0

    def process(self, in_diag, in_y):
        diag = to_signed(in_diag, 32)
        y = to_signed(in_y, 32)
        q = trunc_div_toward_zero(y, diag)
        self.out_x = to_unsigned(q, 32)
        return {"out_x": self.out_x}