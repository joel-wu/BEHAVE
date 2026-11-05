class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 8
        self.window = 0

    def process(self, value):
        state = self.state
        done = int(state == 10)
        out_byte = ((self.window >> 1) & 255) if done else 0
        self.window = (self.window >> 1) | (value << 9)
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
        return {"done": done, "out_byte": out_byte}
