def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ComplexMatrixTransposeTile:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a00_real, in_a00_imag,
        in_a01_real, in_a01_imag,
        in_a10_real, in_a10_imag,
        in_a11_real, in_a11_imag,
    ):
        return {
            "out_b00_real": to_unsigned(in_a00_real, 32),
            "out_b00_imag": to_unsigned(in_a00_imag, 32),
            "out_b01_real": to_unsigned(in_a10_real, 32),
            "out_b01_imag": to_unsigned(in_a10_imag, 32),
            "out_b10_real": to_unsigned(in_a01_real, 32),
            "out_b10_imag": to_unsigned(in_a01_imag, 32),
            "out_b11_real": to_unsigned(in_a11_real, 32),
            "out_b11_imag": to_unsigned(in_a11_imag, 32),
        }