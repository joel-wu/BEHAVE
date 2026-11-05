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
            self.q = ((self.q >> 1) ^ (self.q << 1)) & ((1 << 512) - 1)
        return out
