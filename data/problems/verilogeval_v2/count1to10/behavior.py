class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 1

    def process(self):
        q = self.q
        self.q = 1 if q == 10 else q + 1
        return {"q": q}
