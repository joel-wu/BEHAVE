def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SemiRestrict3Tap1D:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_constant_coefficient, in_r_center, in_r_left, in_r_right, in_Rp0, in_Rp1):
        r_center = to_signed(in_r_center, 32)
        r_left = to_signed(in_r_left, 32)
        r_right = to_signed(in_r_right, 32)
        Rp0 = to_signed(in_Rp0, 32)
        Rp1 = to_signed(in_Rp1, 32)
        left_product = Rp0 * r_left
        right_product = Rp1 * r_right
        full_sum = r_center + left_product + right_product
        return {"out_rc": to_unsigned(full_sum, 32)}