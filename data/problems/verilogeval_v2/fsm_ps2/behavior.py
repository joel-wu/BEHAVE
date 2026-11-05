class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, value):
        state = self.state
        done = int(state == 3)
        if state == 0 or state == 3:
            self.state = 1 if value & 8 else 0
        else:
            self.state = state + 1
        return {"done": done}
