class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b):
        s = (a + b) & 255
        overflow = (((a ^ b) >> 7) ^ 1) & ((a ^ s) >> 7)
        return {"s": s, "overflow": overflow}
