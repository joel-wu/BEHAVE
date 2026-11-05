class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = 0
        self.hits = 0

    def process(self, clear, value):
        out = {"out": self.hits}
        if clear:
            self.hits = 0
        else:
            self.hits |= self.previous & (value ^ 4294967295)
        self.previous = value
        return out
