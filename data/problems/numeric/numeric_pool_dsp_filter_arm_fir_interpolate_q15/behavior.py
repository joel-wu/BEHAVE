def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


COEFFS = [
    0x0100, 0x0200, 0x0300, 0x0400,
    0x0500, 0x0600, 0x0700, 0x0800,
    0x0900, 0x0A00, 0x0B00, 0x0C00,
    0x0D00, 0x0E00, 0x0F00, 0x1000,
    0x1100, 0x1200, 0x1300, 0x1400,
    0x1500, 0x1600, 0x1700, 0x1800,
    0x1900, 0x1A00, 0x1B00, 0x1C00,
    0x1D00, 0x1E00, 0x1F00, 0x2000,
]


class FirInterpolatorQ15L4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0] * 8

    def process(self, in_sample):
        old_state = self.state[:]
        new_sample = to_signed(in_sample, 16)
        state = old_state[1:] + [new_sample]
        outputs = []
        for phase in range(4):
            total = 0
            for k in range(8):
                coeff = to_signed(COEFFS[(3 - phase) + 4 * k], 16)
                total += state[k] * coeff
            shifted = total >> 15
            if shifted > 32767:
                shifted = 32767
            elif shifted < -32768:
                shifted = -32768
            outputs.append({"out_sample": to_unsigned(shifted, 16)})
        self.state = state
        return outputs