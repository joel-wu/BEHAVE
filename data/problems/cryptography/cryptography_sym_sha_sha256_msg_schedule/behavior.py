def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotr32(x, n):
    x = to_unsigned(x, 32)
    return to_unsigned((x >> n) | (x << (32 - n)), 32)


class CryptographySymShaSha256MsgSchedule:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_block):
        w = [0] * 64
        for i in range(16):
            shift = 512 - 32 * (i + 1)
            w[i] = to_unsigned(in_block >> shift, 32)
        for i in range(16, 64):
            x0 = w[i - 15]
            x1 = w[i - 2]
            sigma0 = rotr32(x0, 7) ^ rotr32(x0, 18) ^ (x0 >> 3)
            sigma1 = rotr32(x1, 17) ^ rotr32(x1, 19) ^ (x1 >> 10)
            w[i] = to_unsigned(sigma1 + w[i - 7] + sigma0 + w[i - 16], 32)
        return [{"out_index": to_unsigned(i, 6), "out_word": to_unsigned(w[i], 32)} for i in range(64)]