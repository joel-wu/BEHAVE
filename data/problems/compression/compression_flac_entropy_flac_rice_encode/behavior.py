class CompressionFlacEntropyFlacRiceEncode:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_sample, in_parameter):
        raw = in_sample & 0xFFFFFFFF
        folded = ((raw << 1) & 0xFFFFFFFF) ^ (0xFFFFFFFF if raw & 0x80000000 else 0)
        k = in_parameter & 31
        return {
            "out_codeword": (1 << k) | (folded & ((1 << k) - 1)),
            "out_nbits": (folded >> k) + 1 + k,
        }
