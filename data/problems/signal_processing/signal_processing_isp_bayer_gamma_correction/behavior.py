def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BayerGammaCorrection:
    BIT_DEPTH = 10
    LUT_DEPTH = 1 << BIT_DEPTH

    def __init__(self, BIT_DEPTH=10):
        assert BIT_DEPTH in (8, 10, 12, 14), "BIT_DEPTH must be 8, 10, 12 or 14"
        self.BIT_DEPTH = BIT_DEPTH
        self.LUT_DEPTH = 1 << BIT_DEPTH
        self.reset()

    def reset(self):
        self.gamma_lut = [0] * self.LUT_DEPTH

    def process(self, in_pixel):
        idx = to_unsigned(in_pixel, self.BIT_DEPTH)
        return {"out_pixel": to_unsigned(self.gamma_lut[idx], self.BIT_DEPTH)}
