"""Command interface to a seeded binary-dot-product sequence generator."""


class SignalProcessingCommSequenceMsequence:
    def __init__(self):
        self.reset()

    def reset(self):
        self.m = 2
        self.g = 3
        self.a = 1
        self.state = 1

    def process(self, in_op, in_m, in_g, in_a):
        op = in_op & 3
        bit = 0
        if op == 0:
            m = in_m & 31
            if m >= 2:
                self.m = m
                self.g = in_g & 0x7FFFFFFF
                self.a = in_a & 0x7FFFFFFF
                self.state = self.a
        elif op == 1:
            bit = (self.state & self.g).bit_count() & 1
            self.state = ((self.state << 1) | bit) & ((1 << self.m) - 1)
        elif op == 2:
            self.state = self.a
        return {"out_bit": bit, "out_state": self.state, "out_n": (1 << self.m) - 1}
