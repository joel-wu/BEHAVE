class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load, data):
        out = {"q": self.q}
        if load:
            self.q = data
        else:
            mask = (1 << 512) - 1
            left = self.q >> 1
            right = (self.q << 1) & mask
            self.q = (self.q ^ right) | ((left ^ mask) & self.q)
        return out
