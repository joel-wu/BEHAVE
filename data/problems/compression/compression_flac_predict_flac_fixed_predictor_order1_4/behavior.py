def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CompressionFlacPredictFlacFixedPredictorOrder1_4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sample_z1 = 0
        self.sample_z2 = 0
        self.sample_z3 = 0
        self.sample_z4 = 0

    def process(self, in_order, in_sample):
        x = to_signed(in_sample, 32)
        s1 = self.sample_z1
        s2 = self.sample_z2
        s3 = self.sample_z3
        s4 = self.sample_z4

        if in_order == 1:
            residual = x - s1
        elif in_order == 2:
            residual = x - 2 * s1 + s2
        elif in_order == 3:
            residual = x - 3 * s1 + 3 * s2 - s3
        elif in_order == 4:
            residual = x - 4 * s1 + 6 * s2 - 4 * s3 + s4
        else:
            residual = 0

        self.sample_z4 = s3
        self.sample_z3 = s2
        self.sample_z2 = s1
        self.sample_z1 = x

        return {"out_residual": to_unsigned(residual, 32)}