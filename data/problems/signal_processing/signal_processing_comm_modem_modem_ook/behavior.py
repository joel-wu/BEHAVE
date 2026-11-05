from fp_lib import fp32_cmp, fp32_is_nan

SQRT2 = 0x3FB504F3
SQRT1_2 = 0x3F3504F3
ZERO = 0x00000000


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class OOKModem:
    def __init__(self):
        self.reset()

    def reset(self):
        self.r_re = ZERO
        self.r_im = ZERO
        self.x_hat_re = ZERO
        self.x_hat_im = ZERO

    def process(self, in_op, in_sym, in_x_re, in_x_im):
        if in_op == 0:
            out_sym = in_sym & 1
            out_y_re = SQRT2 if out_sym == 0 else ZERO
            out_y_im = ZERO
            return {
                "out_sym": to_unsigned(out_sym, 1),
                "out_y_re": to_unsigned(out_y_re, 32),
                "out_y_im": to_unsigned(out_y_im, 32),
            }

        gt = 0 if fp32_is_nan(in_x_re) else fp32_cmp(in_x_re, SQRT1_2) > 0
        out_sym = 0 if gt else 1
        out_y_re = SQRT2 if out_sym == 0 else ZERO
        out_y_im = ZERO
        self.r_re = to_unsigned(in_x_re, 32)
        self.r_im = to_unsigned(in_x_im, 32)
        self.x_hat_re = out_y_re
        self.x_hat_im = out_y_im
        return {
            "out_sym": to_unsigned(out_sym, 1),
            "out_y_re": to_unsigned(out_y_re, 32),
            "out_y_im": to_unsigned(out_y_im, 32),
        }