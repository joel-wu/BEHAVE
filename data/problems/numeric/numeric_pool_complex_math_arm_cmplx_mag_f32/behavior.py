from fp_lib import fp32_add, fp32_mul, fp32_sqrt


class ComplexMagnitudeFp32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_real, in_imag):
        real_sq = fp32_mul(in_real, in_real)
        imag_sq = fp32_mul(in_imag, in_imag)
        sum_sq = fp32_add(real_sq, imag_sq)
        out_mag = fp32_sqrt(sum_sq)
        return {"out_mag": out_mag}