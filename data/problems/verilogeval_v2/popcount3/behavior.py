class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        return {"out": (value & 1) + ((value >> 1) & 1) + ((value >> 2) & 1)}
