def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


COEFFS = [
    (8192, 4096, 2048, -4096, 1024),
    (8192, -2048, 1024, -3072, 512),
]
SHIFT = 14


class BiquadCascadeDf1FastQ15:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x1 = [0, 0]
        self.x2 = [0, 0]
        self.y1 = [0, 0]
        self.y2 = [0, 0]

    def process(self, in_x):
        sample = to_signed(in_x, 16)
        for i in range(2):
            x1 = self.x1[i]
            x2 = self.x2[i]
            y1 = self.y1[i]
            y2 = self.y2[i]
            b0, b1, b2, a1, a2 = COEFFS[i]
            acc = to_signed(
                b0 * sample + b1 * x1 + b2 * x2 + a1 * y1 + a2 * y2,
                32,
            )
            shifted = acc >> SHIFT
            upper = shifted >> 15
            low16 = to_signed(shifted, 16)
            if upper == 0:
                stage_out = low16
            elif upper == -1:
                stage_out = low16
            else:
                stage_out = (shifted >> 31) ^ 32767
            self.x2[i] = x1
            self.x1[i] = sample
            self.y2[i] = y1
            self.y1[i] = stage_out
            sample = stage_out
        return {"out_y": to_unsigned(sample, 16)}