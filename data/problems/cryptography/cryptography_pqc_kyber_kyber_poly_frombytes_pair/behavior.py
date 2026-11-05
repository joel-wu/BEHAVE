def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class KyberPolyFrombytesPair:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_packed):
        packed = to_unsigned(in_packed, 24)
        b0 = packed & 0xFF
        b1 = (packed >> 8) & 0xFF
        b2 = (packed >> 16) & 0xFF
        coeff0 = (b0 | (b1 << 8)) & 0xFFF
        coeff1 = ((b1 >> 4) | (b2 << 4)) & 0xFFF
        return {
            "out_coeff0": to_unsigned(coeff0, 12),
            "out_coeff1": to_unsigned(coeff1, 12),
        }