def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ComplexDotProductStreaming:
    def __init__(self):
        self.reset()

    def reset(self):
        self.beat_index = 0
        self.sum_re = 0
        self.sum_im = 0

    def process(self, in_x_re, in_x_im, in_y_re, in_y_im):
        x_re = to_signed(in_x_re, 32)
        x_im = to_signed(in_x_im, 32)
        y_re = to_signed(in_y_re, 32)
        y_im = to_signed(in_y_im, 32)

        product_re = (x_re * y_re) - (x_im * y_im)
        product_im = (x_re * y_im) + (x_im * y_re)

        sum_re = to_signed(self.sum_re, 64)
        sum_im = to_signed(self.sum_im, 64)

        new_sum_re = to_unsigned(sum_re + product_re, 64)
        new_sum_im = to_unsigned(sum_im + product_im, 64)

        if self.beat_index == 7:
            self.beat_index = 0
            self.sum_re = 0
            self.sum_im = 0
            return {"out_z_re": new_sum_re, "out_z_im": new_sum_im}

        self.beat_index += 1
        self.sum_re = new_sum_re
        self.sum_im = new_sum_im
        return None