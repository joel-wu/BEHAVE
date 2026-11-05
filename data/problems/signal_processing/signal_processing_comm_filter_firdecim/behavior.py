def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingCommFilterFirdecim:
    def __init__(self, data_w=16, coef_w=16, scale_w=16, acc_w=48, m=2, coeffs=None, scale=1):
        self.data_w = data_w
        self.coef_w = coef_w
        self.scale_w = scale_w
        self.acc_w = acc_w
        self.m = m
        self.coeffs = [1, 2, 3, 4, 3, 2, 1, 0] if coeffs is None else list(coeffs)
        self.h_len = len(self.coeffs)
        assert data_w >= 1 and coef_w >= 1 and scale_w >= 1 and acc_w >= data_w
        assert m >= 1 and self.h_len >= 1
        self.h = [to_signed(c, self.coef_w) for c in self.coeffs]
        self.scale = to_signed(scale, self.scale_w)
        self.reset()

    def reset(self):
        self.window = [0] * self.h_len
        self.phase = 0

    def process(self, in_x):
        x = to_signed(in_x, self.data_w)
        old_window = self.window
        old_phase = self.phase

        new_window = [x] + old_window[:-1]

        self.window = new_window
        self.phase = 0 if old_phase + 1 == self.m else old_phase + 1

        if old_phase != 0:
            return None

        fir_sum = 0
        for i in range(self.h_len):
            fir_sum += to_signed(new_window[i], self.data_w) * self.h[i]
        scaled_sum = fir_sum * self.scale
        wrapped = to_signed(scaled_sum, self.acc_w)
        return {"out_y": to_unsigned(wrapped, self.data_w)}
