class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        result = 0
        for i in range(100):
            result |= ((value >> i) & 1) << (99 - i)
        return {"out": result}
