class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, y, w):
        Y1 = 0
        if y == 1 or y == 5:
            Y1 = 1
        elif y == 2 or y == 4:
            Y1 = w
        return {"Y1": Y1}
