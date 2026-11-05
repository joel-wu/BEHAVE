class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, x):
        return {"f": (0xD073 >> x) & 1}
