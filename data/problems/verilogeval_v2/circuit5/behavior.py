class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d, e):
        if c == 0:
            q = b
        elif c == 1:
            q = e
        elif c == 2:
            q = a
        elif c == 3:
            q = d
        else:
            q = 15
        return {"q": q}
