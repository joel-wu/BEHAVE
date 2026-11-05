def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlSsmBasedBasedFeatureMap:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = 0

    def process(self, in_q, in_k):
        dot_q30 = 0
        for i in range(8):
            q_i = to_signed((in_q >> (16 * i)) & 0xFFFF, 16)
            k_i = to_signed((in_k >> (16 * i)) & 0xFFFF, 16)
            dot_q30 += q_i * k_i

        x_q30 = dot_q30
        term1_q30 = (1 << 30) + x_q30
        x2_q60 = x_q30 * x_q30
        half_x2_q30 = x2_q60 >> 31
        phi_q30 = term1_q30 + half_x2_q30

        rounded_q15 = (phi_q30 + (1 << 14)) >> 15
        return {"out_s": to_unsigned(rounded_q15, 21)}
