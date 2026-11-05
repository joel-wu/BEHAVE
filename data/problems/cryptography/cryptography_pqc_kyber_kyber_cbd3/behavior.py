def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class cryptography_pqc_kyber_kyber_cbd3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_t):
        d = (in_t & 0x249249) + ((in_t >> 1) & 0x249249) + ((in_t >> 2) & 0x249249)
        c0 = ((d >> 0) & 0x7) - ((d >> 3) & 0x7)
        c1 = ((d >> 6) & 0x7) - ((d >> 9) & 0x7)
        c2 = ((d >> 12) & 0x7) - ((d >> 15) & 0x7)
        c3 = ((d >> 18) & 0x7) - ((d >> 21) & 0x7)
        return {
            "out_c0": to_unsigned(c0, 4),
            "out_c1": to_unsigned(c1, 4),
            "out_c2": to_unsigned(c2, 4),
            "out_c3": to_unsigned(c3, 4),
        }