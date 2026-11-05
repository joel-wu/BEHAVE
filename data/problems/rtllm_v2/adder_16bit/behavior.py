class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, Cin):
        total = a + b + Cin
        return {"y": total & 65535, "Co": total >> 16}
