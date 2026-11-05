def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographySymAeadGhashGf128Mul:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_h, in_y, in_block):
        x = to_unsigned(in_y ^ in_block, 128)
        v = [0] * 128
        v[0] = to_unsigned(in_h, 128)
        r = 0xE1000000000000000000000000000000
        for i in range(1, 128):
            prev = v[i - 1]
            v[i] = to_unsigned((prev >> 1) ^ (r if (prev & 1) == 1 else 0), 128)
        z = 0
        for k in range(128):
            bit = (x >> (127 - k)) & 1
            if bit == 1:
                z ^= v[k]
        return {"out_y": to_unsigned(z, 128)}