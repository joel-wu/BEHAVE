def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ModifiedGivensRotmStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_y, in_dflag, in_dh11, in_dh21, in_dh12, in_dh22):
        x = to_signed(in_x, 64)
        y = to_signed(in_y, 64)
        dflag = to_signed(in_dflag, 64)
        dh11 = to_signed(in_dh11, 64)
        dh21 = to_signed(in_dh21, 64)
        dh12 = to_signed(in_dh12, 64)
        dh22 = to_signed(in_dh22, 64)

        if dflag == -2:
            out_x = x
            out_y = y
        elif dflag < 0:
            p0 = to_signed(x * dh11, 64)
            p1 = to_signed(y * dh12, 64)
            p2 = to_signed(x * dh21, 64)
            p3 = to_signed(y * dh22, 64)
            out_x = to_signed(p0 + p1, 64)
            out_y = to_signed(p2 + p3, 64)
        elif dflag == 0:
            p0 = to_signed(y * dh12, 64)
            p1 = to_signed(x * dh21, 64)
            out_x = to_signed(x + p0, 64)
            out_y = to_signed(p1 + y, 64)
        else:
            p0 = to_signed(x * dh11, 64)
            p1 = to_signed(dh22 * y, 64)
            out_x = to_signed(p0 + y, 64)
            out_y = to_signed((-x) + p1, 64)

        return {"out_x": to_unsigned(out_x, 64), "out_y": to_unsigned(out_y, 64)}