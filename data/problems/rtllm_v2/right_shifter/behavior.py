class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, d):
        out = {"q": self.q}
        self.q = (d << 7) | (self.q >> 1)
        return out
