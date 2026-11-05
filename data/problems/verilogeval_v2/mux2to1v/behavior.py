class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, sel):
        return {"out": b if sel else a}
