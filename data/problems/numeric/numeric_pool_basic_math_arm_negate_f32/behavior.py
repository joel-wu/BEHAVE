from fp_lib import fp32_neg

def to_unsigned(v, b):
    return v & ((1 << b) - 1)

class VectorNegateF32x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a0, in_a1, in_a2, in_a3):
        return {
            "out_y0": to_unsigned(fp32_neg(in_a0), 32),
            "out_y1": to_unsigned(fp32_neg(in_a1), 32),
            "out_y2": to_unsigned(fp32_neg(in_a2), 32),
            "out_y3": to_unsigned(fp32_neg(in_a3), 32),
        }