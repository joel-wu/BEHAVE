def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class LSHSignaturePack:
    def __init__(self, D=32, NBITS=32):
        self.D = D
        self.NBITS = NBITS
        self.reset()

    def reset(self):
        self.code_size = (self.NBITS + 7) // 8

    def process(self, in_x):
        out_bytes = 0
        for j in range(self.NBITS):
            xj = (in_x >> (32 * j)) & 0xFFFFFFFF
            magnitude = xj & 0x7FFFFFFF
            is_nan = magnitude > 0x7F800000
            nonnegative = (xj >> 31) == 0 or magnitude == 0
            out_bytes |= (1 if nonnegative and not is_nan else 0) << j
        return {"out_bytes": to_unsigned(out_bytes, 8 * self.code_size)}
