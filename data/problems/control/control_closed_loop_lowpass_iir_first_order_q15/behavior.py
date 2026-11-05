def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ControlClosedLoopLowpassIirFirstOrderQ15:
    def __init__(self):
        self.reset()

    def reset(self):
        self.y_prev = 0

    def process(self, in_x, in_alpha):
        x = to_signed(in_x, 16)
        alpha = to_signed(in_alpha, 16)
        y_prev = self.y_prev
        one_minus_alpha = 32768 - alpha
        term_prev_q30 = y_prev * alpha
        term_x_q30 = x * one_minus_alpha
        sum_q30 = term_prev_q30 + term_x_q30
        y_raw = sum_q30 >> 15
        if y_raw > 32767:
            y_sat = 32767
        elif y_raw < -32768:
            y_sat = -32768
        else:
            y_sat = y_raw
        self.y_prev = y_sat
        return {"out_y": to_unsigned(y_sat, 16)}
