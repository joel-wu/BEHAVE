class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, x, y):
        return {"z": x | (y ^ 1)}
