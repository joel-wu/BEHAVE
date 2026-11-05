class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, enable, S, A, B, C):
        address = (A << 2) | (B << 1) | C
        out = {"Z": (self.q >> address) & 1}
        if enable:
            self.q = ((self.q << 1) | S) & 255
        return out
