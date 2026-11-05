from fp_lib import fp32_mul


class ComplexRealMulF32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_cmplx_real, in_cmplx_imag, in_real):
        return {
            "out_real": fp32_mul(in_cmplx_real, in_real),
            "out_imag": fp32_mul(in_cmplx_imag, in_real),
        }