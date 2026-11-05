class Parallel2Serial:
    def __init__(self):
        self.reset()

    def reset(self):
        self.data = 0
        self.count = 0
        self.frame_start = 0

    def process(self, d):
        bit = (self.data >> 3) & 1
        frame_start = self.frame_start
        if self.count == 3:
            self.data = d
            self.count = 0
            self.frame_start = 1
        else:
            self.data = ((self.data << 1) | (self.data >> 3)) & 15
            self.count = self.count + 1
            self.frame_start = 0
        return {"valid_out": frame_start, "dout": bit}
