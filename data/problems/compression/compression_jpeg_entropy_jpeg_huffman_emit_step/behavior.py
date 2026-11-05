def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CompressionJpegEntropyJpegHuffmanEmitStep:
    def __init__(self):
        self.reset()

    def reset(self):
        self.put_buffer = 0
        self.free_bits = 64

    def process(self, in_code, in_size):
        raw_size = in_size & 0x7F
        size = raw_size if raw_size <= 16 else 16
        if size == 0:
            return []

        code = to_unsigned(in_code, size)
        put_buffer = self.put_buffer
        free_bits = self.free_bits

        if size <= free_bits:
            self.put_buffer = to_unsigned((put_buffer << size) | code, 64)
            self.free_bits = free_bits - size
            return []

        spill_bits = size - free_bits
        flush_word = to_unsigned((put_buffer << free_bits) | (code >> spill_bits), 64)

        b0 = (flush_word >> 56) & 0xFF
        b1 = (flush_word >> 48) & 0xFF
        b2 = (flush_word >> 40) & 0xFF
        b3 = (flush_word >> 32) & 0xFF
        b4 = (flush_word >> 24) & 0xFF
        b5 = (flush_word >> 16) & 0xFF
        b6 = (flush_word >> 8) & 0xFF
        b7 = flush_word & 0xFF

        out = [{"out_byte": b0}]
        if b0 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b1})
        if b1 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b2})
        if b2 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b3})
        if b3 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b4})
        if b4 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b5})
        if b5 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b6})
        if b6 == 0xFF:
            out.append({"out_byte": 0})
        out.append({"out_byte": b7})
        if b7 == 0xFF:
            out.append({"out_byte": 0})

        self.put_buffer = to_unsigned(code, 64)
        self.free_bits = 64 - spill_bits
        return out