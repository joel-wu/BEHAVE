def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingCommFilterAutocorr:
    def __init__(self):
        self.reset()

    def reset(self):
        self.w = [(0, 0) for _ in range(8)]
        self.wdelay_hist = [(0, 0) for _ in range(12)]
        self.we2 = [0 for _ in range(8)]
        self.e2_sum = 0
        self.ie2 = 0

    def process(self, in_x_re, in_x_im):
        x_re = to_signed(in_x_re, 32)
        x_im = to_signed(in_x_im, 32)

        w = self.w
        wdelay_hist = self.wdelay_hist
        we2 = self.we2
        e2_sum = self.e2_sum
        ie2 = self.ie2

        w_new = [(x_re, x_im)] + w[:-1]
        wdelay_hist_new = [(x_re, -x_im)] + wdelay_hist[:-1]
        wdelay_read = [wdelay_hist_new[4 + k] for k in range(8)]

        rxx_re = 0
        rxx_im = 0
        for k in range(8):
            a, b = w_new[k]
            c, d = wdelay_read[k]
            rxx_re += a * c - b * d
            rxx_im += a * d + b * c

        e2 = x_re * x_re + x_im * x_im
        old_e2 = we2[ie2]
        we2_new = we2[:]
        we2_new[ie2] = e2
        e2_sum_new = e2_sum - old_e2 + e2
        ie2_new = 0 if ie2 == 7 else ie2 + 1

        self.w = w_new
        self.wdelay_hist = wdelay_hist_new
        self.we2 = we2_new
        self.e2_sum = e2_sum_new
        self.ie2 = ie2_new

        return {
            "out_rxx_re": to_unsigned(rxx_re, 68),
            "out_rxx_im": to_unsigned(rxx_im, 68),
            "out_energy": to_unsigned(e2_sum_new, 67),
        }
