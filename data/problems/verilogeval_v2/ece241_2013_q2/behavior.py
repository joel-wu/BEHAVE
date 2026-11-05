class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d):
        return {"out_sop": (c & d) | ((a ^ 1) & (b ^ 1) & c), "out_pos": c & ((b ^ 1) | d) & ((a ^ 1) | d)}
