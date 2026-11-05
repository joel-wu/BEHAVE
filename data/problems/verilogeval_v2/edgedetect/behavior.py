class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0
        self.previous = 0

    def process(self, value):
        out = {"pedge": self.q}
        self.q = value & (self.previous ^ 255)
        self.previous = value
        return out
