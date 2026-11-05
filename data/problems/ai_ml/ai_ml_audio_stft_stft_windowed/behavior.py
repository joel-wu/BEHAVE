def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


WINDOW = [0, 12540, 23170, 30274, 32767, 30274, 23170, 12540]
FRAME_LEN = 8
HOP_LEN = 4
SAMPLE_W = 16
OUT_W = 32


class STFTWindowed:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sample_count = 0
        self.history = [0] * FRAME_LEN

    def process(self, in_sample):
        sample = to_signed(in_sample, SAMPLE_W)
        old_history = self.history
        new_history = old_history[1:] + [sample]
        new_count = FRAME_LEN if self.sample_count == FRAME_LEN + HOP_LEN - 1 else self.sample_count + 1

        emit = new_count == FRAME_LEN

        self.history = new_history
        self.sample_count = new_count

        if not emit:
            return None

        products = [new_history[i] * WINDOW[i] for i in range(FRAME_LEN)]
        return {
            "out_frame_0": to_unsigned(products[0], OUT_W),
            "out_frame_1": to_unsigned(products[1], OUT_W),
            "out_frame_2": to_unsigned(products[2], OUT_W),
            "out_frame_3": to_unsigned(products[3], OUT_W),
            "out_frame_4": to_unsigned(products[4], OUT_W),
            "out_frame_5": to_unsigned(products[5], OUT_W),
            "out_frame_6": to_unsigned(products[6], OUT_W),
            "out_frame_7": to_unsigned(products[7], OUT_W),
        }
