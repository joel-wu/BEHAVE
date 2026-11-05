class MovingAverage:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = [0] * 8
        self.oldest = 0
        self.total = 0

    def process(self, data_in):
        data_out = self.total >> 3
        self.total = self.total - self.history[self.oldest] + data_in
        self.history[self.oldest] = data_in
        self.oldest = (self.oldest + 1) & 7
        return {"data_out": data_out}
