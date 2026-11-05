def mix32(x):
    x = x & 0xffffffff
    x = x ^ (x >> 16)
    x = (x * 0x85ebca6b) & 0xffffffff
    x = x ^ (x >> 13)
    x = (x * 0xc2b2ae35) & 0xffffffff
    return x ^ (x >> 16)


class BloomFilterMultiHashInsertK3:
    def __init__(self):
        self.reset()

    def reset(self):
        self.bits = 0

    def process(self, in_key, in_insert):
        a = mix32(in_key)
        b = mix32((in_key & 0xffffffff) ^ 0x27d4eb2f)
        idx0 = a & 1023
        idx1 = (a + b) & 1023
        idx2 = (a + 2*b) & 1023
        mask = (1 << idx0) | (1 << idx1) | (1 << idx2)
        present = 0
        if (self.bits & mask) == mask:
            present = 1
        if (in_insert & 1) != 0:
            self.bits = self.bits | mask
        return {"out_idx0": idx0, "out_idx1": idx1, "out_idx2": idx2,
                "out_present": present, "out_bits": self.bits}
