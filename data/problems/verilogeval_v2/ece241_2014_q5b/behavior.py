class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.seen = 0

    def process(self, x):
        z = x ^ self.seen
        self.seen = self.seen | x
        return {"z": z}
