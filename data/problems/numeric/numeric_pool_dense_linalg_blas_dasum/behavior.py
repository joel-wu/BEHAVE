def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class VectorAbsoluteSum6:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_sum = 0

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5):
        x0 = to_signed(in_x0, 32)
        x1 = to_signed(in_x1, 32)
        x2 = to_signed(in_x2, 32)
        x3 = to_signed(in_x3, 32)
        x4 = to_signed(in_x4, 32)
        x5 = to_signed(in_x5, 32)

        abs_0 = x0 if x0 >= 0 else -x0
        abs_1 = x1 if x1 >= 0 else -x1
        abs_2 = x2 if x2 >= 0 else -x2
        abs_3 = x3 if x3 >= 0 else -x3
        abs_4 = x4 if x4 >= 0 else -x4
        abs_5 = x5 if x5 >= 0 else -x5

        sum0 = abs_0
        sum1 = sum0 + abs_1
        sum2 = sum1 + abs_2
        sum3 = sum2 + abs_3
        sum4 = sum3 + abs_4
        sum5 = sum4 + abs_5

        self.out_sum = to_unsigned(sum5, 35)
        return {"out_sum": self.out_sum}