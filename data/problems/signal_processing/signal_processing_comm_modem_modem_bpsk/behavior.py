def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class BPSKModem:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x_hat_real = 0
        self.r_real = 0

    def process(self, in_op, in_sym, in_x_real):
        x_hat_real_current = self.x_hat_real
        r_real_current = self.r_real

        if in_op & 1:
            x_real = to_signed(in_x_real, 32)
            out_sym = 0 if x_real > 0 else 1
            out_y_real = 1 if out_sym == 0 else -1
            x_hat_real_next = out_y_real
            r_real_next = x_real
        else:
            out_sym = 0
            out_y_real = -1 if (in_sym & 1) else 1
            x_hat_real_next = x_hat_real_current
            r_real_next = r_real_current

        self.x_hat_real = x_hat_real_next
        self.r_real = r_real_next

        return {
            "out_sym": to_unsigned(out_sym, 1),
            "out_y_real": to_unsigned(out_y_real, 32),
            "out_x_hat_real": to_unsigned(x_hat_real_next, 32),
            "out_r_real": to_unsigned(r_real_next, 32),
        }