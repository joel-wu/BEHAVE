def signed16(value):
    return value - 65536 if value & 32768 else value


class ComplexMultiplier:
    def __init__(self):
        self.reset()

    def reset(self):
        self.real = 0
        self.imag = 0

    def process(self, a_real, a_imag, b_real, b_imag):
        old_real = self.real
        old_imag = self.imag
        a = signed16(a_real)
        b = signed16(a_imag)
        c = signed16(b_real)
        d = signed16(b_imag)
        self.real = (a * c - b * d) & 0xffffffff
        self.imag = (a * d + b * c) & 0xffffffff
        return {"result_real": old_real, "result_imag": old_imag}
