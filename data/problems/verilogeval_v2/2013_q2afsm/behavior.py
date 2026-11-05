class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, r):
        state = self.state
        g = (1 << (state - 1)) if state else 0
        if state == 0:
            self.state = 1 if r & 1 else 2 if r & 2 else 3 if r & 4 else 0
        elif not (r & (1 << (state - 1))):
            self.state = 0
        return {"g": g}
