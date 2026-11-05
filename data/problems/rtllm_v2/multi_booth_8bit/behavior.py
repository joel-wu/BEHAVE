class MultiBooth8Bit:
    def __init__(self):
        self.reset()

    def reset(self):
        self.multiplier = 0
        self.multiplicand = 0
        self.product = 0
        self.count = 0
        self.ready = 0

    def process(self, load, a, b):
        out = {"p": self.product, "rdy": self.ready}
        if load:
            self.multiplier = a | (65280 if a >= 128 else 0)
            self.multiplicand = b | (65280 if b >= 128 else 0)
            self.product = 0
            self.count = 0
            self.ready = 0
        elif self.count < 16:
            if (self.multiplier >> self.count) & 1:
                self.product = (self.product + self.multiplicand) & 65535
            self.multiplicand = (self.multiplicand << 1) & 65535
            self.count += 1
        else:
            self.ready = 1
        return out
