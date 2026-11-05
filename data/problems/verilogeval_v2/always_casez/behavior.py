class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        pos = 0
        for i in range(8):
            bit = 7 - i
            if (value >> bit) & 1:
                pos = bit
        return {"pos": pos}
