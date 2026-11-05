class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in1, in2, in3):
        return {"out": in1 ^ in2 ^ in3 ^ 1}
