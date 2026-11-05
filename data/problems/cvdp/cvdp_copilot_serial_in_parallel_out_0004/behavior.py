class SerialInParallelOut8bit:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, serial_in):
        out = {"parallel_out": self.q}
        self.q = ((self.q << 1) | serial_in) & 255
        return out
