class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self):
        q = self.q
        self.q = (q + 1) & 15
        return {"q": q}
