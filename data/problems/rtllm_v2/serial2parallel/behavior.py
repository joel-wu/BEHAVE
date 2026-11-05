"""Eight enabled bits form one byte; physical output timing is in the interface."""
class SerialToParallel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.shift_reg = 0
        self.bit_count = 0

    def process(self, din_serial):
        self.shift_reg = ((self.shift_reg << 1) | (din_serial & 1)) & 255
        if self.bit_count == 7:
            self.bit_count = 0
            return {"dout_parallel": self.shift_reg}
        self.bit_count += 1
        return None
