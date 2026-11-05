class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, a, b):
        out = {"q": a ^ b ^ self.q, "state": self.q}
        self.q = (a & b) | (a & self.q) | (b & self.q)
        return out
