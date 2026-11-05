from fp_lib import fp32_add


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class MatrixAddF32x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a00, in_a01, in_a10, in_a11,
        in_b00, in_b01, in_b10, in_b11
    ):
        return {
            "out_c00": to_unsigned(fp32_add(in_a00, in_b00), 32),
            "out_c01": to_unsigned(fp32_add(in_a01, in_b01), 32),
            "out_c10": to_unsigned(fp32_add(in_a10, in_b10), 32),
            "out_c11": to_unsigned(fp32_add(in_a11, in_b11), 32),
        }