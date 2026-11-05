class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.a = 0
        self.b = 0
        self.c = 0

    def process(self, x):
        out = {"z": int(self.a == 0 and self.b == 0 and self.c == 0)}
        self.a = self.a ^ x
        self.b = x & (self.b ^ 1)
        self.c = x | (self.c ^ 1)
        return out
