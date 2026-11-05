class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 1

    def process(self):
        q = self.q
        self.q = (q >> 1) ^ (2149580803 if q & 1 else 0)
        return {"q": q}
