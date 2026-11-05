class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value, ctrl):
        return {"out": value >> ctrl}
