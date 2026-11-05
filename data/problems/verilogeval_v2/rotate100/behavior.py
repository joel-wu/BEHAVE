class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load, ena, data):
        out = {"q": self.q}
        if load:
            self.q = data
        elif ena == 1:
            self.q = (self.q >> 1) | ((self.q & 1) << 99)
        elif ena == 2:
            self.q = ((self.q << 1) | (self.q >> 99)) & ((1 << 100) - 1)
        return out
