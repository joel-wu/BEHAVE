class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 8

    def process(self, value):
        state = self.state
        done = int(state == 10)
        if state == 8 or state == 10:
            self.state = 8 if value else 0
        elif state < 7:
            self.state = state + 1
        elif state == 7:
            self.state = 9
        elif state == 9:
            self.state = 10 if value else 11
        else:
            self.state = 8 if value else 11
        return {"done": done}
