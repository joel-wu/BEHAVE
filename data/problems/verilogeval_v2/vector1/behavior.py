class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        return {"out_hi": value >> 8, "out_lo": value & 255}
