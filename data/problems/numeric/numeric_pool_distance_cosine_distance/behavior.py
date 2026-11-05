from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_sqrt, fp32_sub


class CosineDistanceFp32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.vec_a = [0] * 256
        self.vec_b = [0] * 256

    def process(self, in_base_a, in_base_b):
        a = [self.vec_a[(in_base_a + i) & 0xFF] for i in range(16)]
        b = [self.vec_b[(in_base_b + i) & 0xFF] for i in range(16)]

        power_a = fp32_mul(a[0], a[0])
        power_a = fp32_add(power_a, fp32_mul(a[1], a[1]))
        power_a = fp32_add(power_a, fp32_mul(a[2], a[2]))
        power_a = fp32_add(power_a, fp32_mul(a[3], a[3]))
        power_a = fp32_add(power_a, fp32_mul(a[4], a[4]))
        power_a = fp32_add(power_a, fp32_mul(a[5], a[5]))
        power_a = fp32_add(power_a, fp32_mul(a[6], a[6]))
        power_a = fp32_add(power_a, fp32_mul(a[7], a[7]))
        power_a = fp32_add(power_a, fp32_mul(a[8], a[8]))
        power_a = fp32_add(power_a, fp32_mul(a[9], a[9]))
        power_a = fp32_add(power_a, fp32_mul(a[10], a[10]))
        power_a = fp32_add(power_a, fp32_mul(a[11], a[11]))
        power_a = fp32_add(power_a, fp32_mul(a[12], a[12]))
        power_a = fp32_add(power_a, fp32_mul(a[13], a[13]))
        power_a = fp32_add(power_a, fp32_mul(a[14], a[14]))
        power_a = fp32_add(power_a, fp32_mul(a[15], a[15]))

        power_b = fp32_mul(b[0], b[0])
        power_b = fp32_add(power_b, fp32_mul(b[1], b[1]))
        power_b = fp32_add(power_b, fp32_mul(b[2], b[2]))
        power_b = fp32_add(power_b, fp32_mul(b[3], b[3]))
        power_b = fp32_add(power_b, fp32_mul(b[4], b[4]))
        power_b = fp32_add(power_b, fp32_mul(b[5], b[5]))
        power_b = fp32_add(power_b, fp32_mul(b[6], b[6]))
        power_b = fp32_add(power_b, fp32_mul(b[7], b[7]))
        power_b = fp32_add(power_b, fp32_mul(b[8], b[8]))
        power_b = fp32_add(power_b, fp32_mul(b[9], b[9]))
        power_b = fp32_add(power_b, fp32_mul(b[10], b[10]))
        power_b = fp32_add(power_b, fp32_mul(b[11], b[11]))
        power_b = fp32_add(power_b, fp32_mul(b[12], b[12]))
        power_b = fp32_add(power_b, fp32_mul(b[13], b[13]))
        power_b = fp32_add(power_b, fp32_mul(b[14], b[14]))
        power_b = fp32_add(power_b, fp32_mul(b[15], b[15]))

        dot = fp32_mul(a[0], b[0])
        dot = fp32_add(dot, fp32_mul(a[1], b[1]))
        dot = fp32_add(dot, fp32_mul(a[2], b[2]))
        dot = fp32_add(dot, fp32_mul(a[3], b[3]))
        dot = fp32_add(dot, fp32_mul(a[4], b[4]))
        dot = fp32_add(dot, fp32_mul(a[5], b[5]))
        dot = fp32_add(dot, fp32_mul(a[6], b[6]))
        dot = fp32_add(dot, fp32_mul(a[7], b[7]))
        dot = fp32_add(dot, fp32_mul(a[8], b[8]))
        dot = fp32_add(dot, fp32_mul(a[9], b[9]))
        dot = fp32_add(dot, fp32_mul(a[10], b[10]))
        dot = fp32_add(dot, fp32_mul(a[11], b[11]))
        dot = fp32_add(dot, fp32_mul(a[12], b[12]))
        dot = fp32_add(dot, fp32_mul(a[13], b[13]))
        dot = fp32_add(dot, fp32_mul(a[14], b[14]))
        dot = fp32_add(dot, fp32_mul(a[15], b[15]))

        power_product = fp32_mul(power_a, power_b)
        norm = fp32_sqrt(power_product)
        ratio = fp32_div(dot, norm)
        out_distance = fp32_sub(0x3F800000, ratio)
        return {"out_distance": out_distance}