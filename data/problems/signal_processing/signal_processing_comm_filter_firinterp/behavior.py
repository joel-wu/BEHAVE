def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class FIRInterp:
    def __init__(self, m=2, data_width=16, coef_width=16, coef=None):
        self.M = m
        self.data_width = data_width
        self.coef_width = coef_width
        self.coef = [1, 2, 3, 4, 3, 2, 1] if coef is None else list(coef)
        self.coef_len_in = len(self.coef)
        assert m >= 2 and self.coef_len_in >= m
        assert data_width >= 1 and coef_width >= 1
        self.h_sub_len = (self.coef_len_in + self.M - 1) // self.M
        self.h_len = self.M * self.h_sub_len
        self.h = [
            to_signed(self.coef[i], self.coef_width) if i < self.coef_len_in else 0
            for i in range(self.h_len)
        ]
        self.reset()

    def reset(self):
        self.state = [0] * self.h_sub_len

    def process(self, in_x):
        x = to_signed(in_x, self.data_width)
        old_state = self.state
        new_state = [x] + old_state[:-1]
        outputs = []
        for p in range(self.M):
            acc_p = 0
            for k in range(self.h_sub_len):
                acc_p += new_state[k] * self.h[p + k * self.M]
            outputs.append({"out_y": to_unsigned(acc_p, self.data_width)})
        self.state = new_state
        return outputs
