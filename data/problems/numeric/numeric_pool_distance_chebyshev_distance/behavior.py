from fp_lib import fp32_abs, fp32_cmp, fp32_sub


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ChebyshevDistanceF32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.vec_a = [0] * 256
        self.vec_b = [0] * 256

    def process(self, in_a_base, in_b_base, in_block_size):
        in_a_base = to_unsigned(in_a_base, 8)
        in_b_base = to_unsigned(in_b_base, 8)
        in_block_size = to_unsigned(in_block_size, 8)
        if in_block_size == 0:
            return {"out_distance": 0}
        a0 = self.vec_a[to_unsigned(in_a_base, 8)]
        b0 = self.vec_b[to_unsigned(in_b_base, 8)]
        running_max = fp32_abs(fp32_sub(a0, b0))
        for i in range(1, 256):
            if i >= in_block_size:
                break
            ai = self.vec_a[to_unsigned(in_a_base + i, 8)]
            bi = self.vec_b[to_unsigned(in_b_base + i, 8)]
            diff_i = fp32_abs(fp32_sub(ai, bi))
            if fp32_cmp(diff_i, running_max) > 0:
                running_max = diff_i
        return {"out_distance": to_unsigned(running_max, 32)}