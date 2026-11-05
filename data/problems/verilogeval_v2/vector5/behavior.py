class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d, e):
        word = (a << 4) | (b << 3) | (c << 2) | (d << 1) | e
        result = 0
        for i in range(5):
            for j in range(5):
                bit = (((word >> i) ^ (word >> j)) & 1) ^ 1
                result |= bit << (5 * i + j)
        return {"out": result}
