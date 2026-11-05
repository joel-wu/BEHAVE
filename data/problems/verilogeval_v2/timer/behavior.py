class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load, data):
        out = {"tc": int(self.q == 0)}
        if load:
            self.q = data
        elif self.q != 0:
            self.q -= 1
        return out
