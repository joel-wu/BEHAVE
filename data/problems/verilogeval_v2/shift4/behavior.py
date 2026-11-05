class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load, ena, data):
        q = self.q
        if load:
            self.q = data
        elif ena:
            self.q = q >> 1
        return {"q": q}
