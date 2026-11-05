class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d, e, f):
        word = (a << 27) | (b << 22) | (c << 17) | (d << 12) | (e << 7) | (f << 2) | 3
        return {"w": word >> 24, "x": (word >> 16) & 255, "y": (word >> 8) & 255, "z": word & 255}
