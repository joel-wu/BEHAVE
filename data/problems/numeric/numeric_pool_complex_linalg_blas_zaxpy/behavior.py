def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ComplexAxpyStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_alpha_re, in_alpha_im, in_x_re, in_x_im, in_y_re, in_y_im):
        alpha_re = to_signed(in_alpha_re, 64)
        alpha_im = to_signed(in_alpha_im, 64)
        x_re = to_signed(in_x_re, 64)
        x_im = to_signed(in_x_im, 64)
        y_re = to_signed(in_y_re, 64)
        y_im = to_signed(in_y_im, 64)

        abs_alpha_re = alpha_re if alpha_re >= 0 else -alpha_re
        abs_alpha_im = alpha_im if alpha_im >= 0 else -alpha_im
        cabs1_alpha = abs_alpha_re + abs_alpha_im

        if cabs1_alpha == 0:
            return {
                "out_y_re": to_unsigned(y_re, 64),
                "out_y_im": to_unsigned(y_im, 64),
            }

        prod_re = (alpha_re * x_re) - (alpha_im * x_im)
        prod_im = (alpha_re * x_im) + (alpha_im * x_re)
        sum_re = y_re + prod_re
        sum_im = y_im + prod_im

        return {
            "out_y_re": to_unsigned(sum_re, 64),
            "out_y_im": to_unsigned(sum_im, 64),
        }