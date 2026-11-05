class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        result = 0
        for lane in range(4):
            result |= ((value >> (8 * lane)) & 255) << (8 * (3 - lane))
        return {"out": result}
