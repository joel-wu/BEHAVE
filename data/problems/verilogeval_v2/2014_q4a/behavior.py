class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, w, R, E, L):
        out = {"Q": self.q}
        if L:
            self.q = R
        elif E:
            self.q = w
        return out
