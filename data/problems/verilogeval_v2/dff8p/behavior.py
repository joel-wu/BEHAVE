class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 52

    def process(self, d):
        q = self.q
        self.q = d
        return {"q": q}
