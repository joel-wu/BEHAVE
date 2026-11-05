def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotr32(x, n):
    x = to_unsigned(x, 32)
    return to_unsigned((x >> n) | (x << (32 - n)), 32)


class CryptographySymShaSha256SigmaCaps:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x):
        x = to_unsigned(in_x, 32)
        out_sigma0 = rotr32(x, 2) ^ rotr32(x, 13) ^ rotr32(x, 22)
        out_sigma1 = rotr32(x, 6) ^ rotr32(x, 11) ^ rotr32(x, 25)
        return {
            "out_sigma0": to_unsigned(out_sigma0, 32),
            "out_sigma1": to_unsigned(out_sigma1, 32),
        }