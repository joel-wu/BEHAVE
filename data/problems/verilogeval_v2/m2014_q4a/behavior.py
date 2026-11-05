class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, d, ena):
        if ena:
            self.q = d
        return {"q": self.q}
