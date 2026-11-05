class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, p1a, p1b, p1c, p1d, p1e, p1f, p2a, p2b, p2c, p2d):
        return {"p1y": (p1a & p1b & p1c) | (p1d & p1e & p1f), "p2y": (p2a & p2b) | (p2c & p2d)}
