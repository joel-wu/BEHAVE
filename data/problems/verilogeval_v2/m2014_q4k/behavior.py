class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, value):
        out = (self.q >> 3) & 1
        self.q = ((self.q << 1) | value) & 15
        return {"out": out}
