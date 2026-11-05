class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        return {"out": value ^ 1}
