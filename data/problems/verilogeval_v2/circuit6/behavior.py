class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a):
        q = 0
        if a == 0:
            q = 4658
        elif a == 1:
            q = 44768
        elif a == 2:
            q = 10196
        elif a == 3:
            q = 23054
        elif a == 4:
            q = 8294
        elif a == 5:
            q = 25806
        elif a == 6:
            q = 50470
        elif a == 7:
            q = 12057
        return {"q": q}
