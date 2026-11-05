class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, x):
        return {"f": (0x5850 >> x) & 1}
