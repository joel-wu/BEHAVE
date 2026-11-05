class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        total = A + B
        return {"S": total & 4294967295, "C32": total >> 32}
