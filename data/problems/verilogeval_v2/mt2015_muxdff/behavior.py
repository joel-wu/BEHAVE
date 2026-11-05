class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, L, q_in, r_in):
        out = {"Q": self.q}
        self.q = r_in if L else q_in
        return out
