class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        result = 0
        for i in range(8):
            result |= ((value >> i) & 1) << (7 - i)
        return {"out": result}
