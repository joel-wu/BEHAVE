class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, x, y):
        state = self.state
        f = int(state == 1)
        g = int(state == 5 or state == 6 or state == 8)
        if state < 2:
            self.state = state + 1
        elif state == 2:
            self.state = 3 if x else 2
        elif state == 3:
            self.state = 3 if x else 4
        elif state == 4:
            self.state = 5 if x else 2
        elif state == 5:
            self.state = 8 if y else 6
        elif state == 6:
            self.state = 8 if y else 7
        return {"f": f, "g": g}
