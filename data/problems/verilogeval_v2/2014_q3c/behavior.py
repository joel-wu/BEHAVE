class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, x, y):
        if y == 0 or y == 2:
            Y0 = x
        elif y == 1 or y == 3 or y == 4:
            Y0 = x ^ 1
        else:
            Y0 = 0
        return {"Y0": Y0, "z": int(y == 3 or y == 4)}
