class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, a, b):
        x = a ^ b
        out = {"out_assign": x, "out_always_comb": x, "out_always_ff": self.q}
        self.q = x
        return out
