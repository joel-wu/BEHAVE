class CompressionFlacEntropyFlacUnaryEncode:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending_count = 0
        self.pending_value = 0

    def process(self, in_val, in_flush):
        count = in_val & 255
        bits = (self.pending_value << (count + 1)) | 1
        length = self.pending_count + count + 1
        if in_flush:
            padding = (-length) & 7
            bits <<= padding
            length += padding

        outputs = []
        for index in range(length // 8):
            shift = length - 8 * (index + 1)
            outputs.append({"out_byte": (bits >> shift) & 255})

        self.pending_count = length & 7
        self.pending_value = bits & ((1 << self.pending_count) - 1)
        return outputs
