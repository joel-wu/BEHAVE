def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


COEFFS = [0x1000, 0x1000, 0x1000, 0x1000, 0x1000, 0x1000, 0x1000, 0x1000]


class FirQ15Sample:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0] * 8

    def process(self, in_sample):
        sample = to_signed(in_sample, 16)
        old_state = self.state
        new_state = [sample, old_state[0], old_state[1], old_state[2], old_state[3], old_state[4], old_state[5], old_state[6]]
        acc = 0
        for i in range(8):
            acc += to_signed(new_state[i], 16) * to_signed(COEFFS[i], 16)
        shifted = acc >> 15
        out_sample = (shifted if shifted < 32767 else 32767) if shifted > -32768 else -32768
        self.state = new_state
        return {"out_sample": to_unsigned(out_sample, 16)}