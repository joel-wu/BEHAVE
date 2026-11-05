class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, a):
        out = {"q": self.q}
        self.q = a ^ 1
        return out
