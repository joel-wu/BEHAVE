class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, byteena, d):
        q = self.q
        if byteena & 1:
            self.q = (self.q & 65280) | (d & 255)
        if byteena & 2:
            self.q = (self.q & 255) | (d & 65280)
        return {"q": q}
