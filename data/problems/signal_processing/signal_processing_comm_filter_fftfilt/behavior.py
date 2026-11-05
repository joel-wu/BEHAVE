def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class signal_processing_comm_filter_fftfilt:
    def __init__(self, coef=None):
        values = [1, -2, 3, 1] if coef is None else list(coef)
        assert len(values) == 4
        self.coeffs = [to_signed(v, 32) for v in values]
        self.reset()

    def reset(self):
        self.delay_line = [0, 0, 0, 0]

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7):
        x = [
            to_signed(in_x0, 32),
            to_signed(in_x1, 32),
            to_signed(in_x2, 32),
            to_signed(in_x3, 32),
            to_signed(in_x4, 32),
            to_signed(in_x5, 32),
            to_signed(in_x6, 32),
            to_signed(in_x7, 32),
        ]
        delay = list(self.delay_line)
        outs = []
        for k in range(8):
            # shift in x[k]
            delay = [x[k], delay[0], delay[1], delay[2]]
            acc = 0
            for j in range(4):
                acc += self.coeffs[j] * delay[j]
            outs.append(to_unsigned(acc, 32))

        self.delay_line = [to_signed(v, 32) for v in delay]

        return {
            "out_y0": outs[0],
            "out_y1": outs[1],
            "out_y2": outs[2],
            "out_y3": outs[3],
            "out_y4": outs[4],
            "out_y5": outs[5],
            "out_y6": outs[6],
            "out_y7": outs[7],
        }
