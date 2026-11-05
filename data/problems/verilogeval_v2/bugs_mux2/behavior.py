class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, sel, a, b):
        return {"out": b if sel else a}
