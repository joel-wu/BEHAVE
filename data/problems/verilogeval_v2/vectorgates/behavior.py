class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b):
        return {"out_or_bitwise": a | b, "out_or_logical": 1 if a or b else 0, "out_not": ((b ^ 7) << 3) | (a ^ 7)}
