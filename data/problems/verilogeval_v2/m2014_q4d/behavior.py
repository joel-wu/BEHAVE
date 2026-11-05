class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, value):
        out = {"out": self.q}
        self.q = self.q ^ value
        return out
