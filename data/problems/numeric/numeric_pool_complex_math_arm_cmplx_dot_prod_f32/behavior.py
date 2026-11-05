from fp_lib import fp32_add, fp32_sub, fp32_mul

def to_unsigned(v, b):
    return v & ((1 << b) - 1)

class ComplexDotProductF32x4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.vec_a = [0] * 256
        self.vec_b = [0] * 256

    def process(self, in_base_a, in_base_b):
        base_a = to_unsigned(in_base_a, 8)
        base_b = to_unsigned(in_base_b, 8)

        real_sum = 0x00000000
        imag_sum = 0x00000000
        for k in range(4):
            ar = self.vec_a[(base_a + 2*k) & 255]
            ai = self.vec_a[(base_a + 2*k + 1) & 255]
            br = self.vec_b[(base_b + 2*k) & 255]
            bi = self.vec_b[(base_b + 2*k + 1) & 255]
            # Each product enters its accumulator before the next product.
            real_sum = fp32_add(real_sum, fp32_mul(ar, br))
            imag_sum = fp32_add(imag_sum, fp32_mul(ar, bi))
            real_sum = fp32_sub(real_sum, fp32_mul(ai, bi))
            imag_sum = fp32_add(imag_sum, fp32_mul(ai, br))
        return {"out_real": real_sum, "out_imag": imag_sum}
