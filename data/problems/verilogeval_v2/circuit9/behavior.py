class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, a):
        out = {"q": self.q}
        if a:
            self.q = 4
        elif self.q == 6:
            self.q = 0
        else:
            self.q = (self.q + 1) & 7
        return out
