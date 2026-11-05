def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class KyberDecompressD4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_byte):
        a = to_unsigned(in_byte, 8)
        t0 = a & 0xF
        t1 = (a >> 4) & 0xF
        coeff0 = ((t0 * 3329) + 8) >> 4
        coeff1 = ((t1 * 3329) + 8) >> 4
        return {
            "out_coeff0": to_unsigned(coeff0, 12),
            "out_coeff1": to_unsigned(coeff1, 12),
        }