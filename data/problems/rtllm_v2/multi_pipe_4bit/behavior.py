class MultiPipe4Bit:
    def __init__(self, size=4):
        self.size = size
        self.reset()

    def reset(self):
        self.product = 0
        self.output = 0

    def process(self, mul_a, mul_b):
        result = {"mul_out": self.output}
        mask = (1 << self.size) - 1
        self.output = self.product
        self.product = (mul_a & mask) * (mul_b & mask)
        return result
