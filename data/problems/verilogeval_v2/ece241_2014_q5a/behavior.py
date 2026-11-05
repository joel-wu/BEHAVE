class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.seen = 0
        self.z = 0

    def process(self, x):
        z = self.z
        self.z = x ^ self.seen
        self.seen = self.seen | x
        return {"z": z}
