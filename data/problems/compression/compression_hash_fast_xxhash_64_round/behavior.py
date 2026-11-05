def to_unsigned(v, b):
    return v & ((1 << b) - 1)


PRIME64_1 = 0x9E3779B185EBCA87
PRIME64_2 = 0xC2B2AE3D27D4EB4F


class CompressionHashFastXxhash64Round:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_acc, in_input):
        product64 = to_unsigned(in_input * PRIME64_2, 64)
        sum64 = to_unsigned(in_acc + product64, 64)
        rot64 = to_unsigned((sum64 << 31) | (sum64 >> 33), 64)
        result = to_unsigned(rot64 * PRIME64_1, 64)
        return {"out_acc": result}