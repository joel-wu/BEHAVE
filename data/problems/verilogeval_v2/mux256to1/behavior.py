class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value, sel):
        return {"out": (value >> sel) & 1}
