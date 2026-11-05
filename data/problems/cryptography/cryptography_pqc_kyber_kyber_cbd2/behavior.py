def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyPqcKyberKyberCbd2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_b0, in_b1, in_b2, in_b3):
        t = (
            to_unsigned(in_b0, 8)
            | (to_unsigned(in_b1, 8) << 8)
            | (to_unsigned(in_b2, 8) << 16)
            | (to_unsigned(in_b3, 8) << 24)
        )
        d = (t & 0x55555555) + ((t >> 1) & 0x55555555)
        outputs = []
        for j in range(8):
            a_j = (d >> (4 * j)) & 0x3
            b_j = (d >> (4 * j + 2)) & 0x3
            coeff_j = a_j - b_j
            outputs.append({"out_coeff": to_unsigned(coeff_j, 4)})
        return outputs
