class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b):
        return {"out": (a | b) ^ 1}
