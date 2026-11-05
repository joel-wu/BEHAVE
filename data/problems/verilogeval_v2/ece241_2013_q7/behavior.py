class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, j, k):
        out = {"Q": self.q}
        self.q = (j & (self.q ^ 1)) | ((k ^ 1) & self.q)
        return out
