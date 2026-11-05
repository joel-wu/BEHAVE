class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self):
        q = self.q
        self.q = 0 if q == 999 else q + 1
        return {"q": q}
