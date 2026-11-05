class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        return {"out": value | (4294967040 if value & 128 else 0)}
