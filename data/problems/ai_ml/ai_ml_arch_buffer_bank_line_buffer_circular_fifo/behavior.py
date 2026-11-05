def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BufferBankLineBufferCircularFifo:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mem = [0] * 16

    def process(self, in_write, in_addr, in_fromDMA, in_mask, in_data):
        addr = in_addr & 0xF
        if in_write:
            if in_mask & 1:
                self.mem[addr] = to_unsigned(in_data, 32)
            return None
        return {
            "out_data": self.mem[addr],
            "out_fromDMA": to_unsigned(in_fromDMA, 1),
        }
