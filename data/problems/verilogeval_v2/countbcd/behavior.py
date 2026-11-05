class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0

    def process(self):
        value = self.count
        q = (value % 10) | ((value // 10 % 10) << 4) | ((value // 100 % 10) << 8) | ((value // 1000) << 12)
        ena = int(value % 10 == 9) | (int(value % 100 == 99) << 1) | (int(value % 1000 == 999) << 2)
        self.count = 0 if value == 9999 else value + 1
        return {"ena": ena, "q": q}
