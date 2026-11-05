def to_unsigned(v, b):
    return v & ((1 << b) - 1)


P256 = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF


class CryptographyPkClassicP256PartialReduce:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x):
        return {"out_r": to_unsigned(in_x % P256, 256)}