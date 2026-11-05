from fp_lib import fp32_add, fp32_sub, fp32_mul


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ComplexFp32Multiply:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a_real, in_a_imag, in_b_real, in_b_imag):
        product_rr = fp32_mul(in_a_real, in_b_real)
        product_ii = fp32_mul(in_a_imag, in_b_imag)
        product_ri = fp32_mul(in_a_real, in_b_imag)
        product_ir = fp32_mul(in_a_imag, in_b_real)
        out_real = fp32_sub(product_rr, product_ii)
        out_imag = fp32_add(product_ri, product_ir)
        return {
            "out_real": to_unsigned(out_real, 32),
            "out_imag": to_unsigned(out_imag, 32),
        }