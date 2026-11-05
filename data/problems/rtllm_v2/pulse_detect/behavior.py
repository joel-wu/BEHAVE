"""Mealy 010 detector: one call per non-reset rising-edge sample."""
class PulseDetect:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = 0
        self.seen = 0

    def process(self, data_in):
        bit = data_in & 1
        pulse = 1 if self.seen == 2 and self.history == 1 and bit == 0 else 0
        self.history = ((self.history << 1) | bit) & 3
        if self.seen < 2:
            self.seen += 1
        return {"data_out": pulse}
