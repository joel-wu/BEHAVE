class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, p1a, p1b, p1c, p1d, p2a, p2b, p2c, p2d):
        return {"p1y": (p1a & p1b & p1c & p1d) ^ 1, "p2y": (p2a & p2b & p2c & p2d) ^ 1}
