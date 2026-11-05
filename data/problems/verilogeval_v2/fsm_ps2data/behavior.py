class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0
        self.window = 0

    def process(self, value):
        state = self.state
        done = int(state == 3)
        out_bytes = self.window if done else 0
        self.window = ((self.window << 8) | value) & 16777215
        if state == 0 or state == 3:
            self.state = 1 if value & 8 else 0
        else:
            self.state = state + 1
        return {"done": done, "out_bytes": out_bytes}
