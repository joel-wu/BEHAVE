class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d, e, f, g, h, i, sel):
        out = 65535
        if sel == 0:
            out = a
        elif sel == 1:
            out = b
        elif sel == 2:
            out = c
        elif sel == 3:
            out = d
        elif sel == 4:
            out = e
        elif sel == 5:
            out = f
        elif sel == 6:
            out = g
        elif sel == 7:
            out = h
        elif sel == 8:
            out = i
        return {"out": out}
