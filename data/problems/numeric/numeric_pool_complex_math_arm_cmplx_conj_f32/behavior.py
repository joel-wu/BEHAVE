def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ComplexConjugateF32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_real, in_imag):
        return {
            "out_real": to_unsigned(in_real, 32),
            "out_imag": to_unsigned(in_imag ^ 0x80000000, 32),
        }