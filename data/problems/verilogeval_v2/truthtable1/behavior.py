class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, x3, x2, x1):
        return {"f": (x3 & x1) | ((x3 ^ 1) & x2)}
