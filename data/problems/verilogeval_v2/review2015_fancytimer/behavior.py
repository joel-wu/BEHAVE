class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0
        self.delay = 0
        self.fast = 0

    def process(self, data, ack):
        state = self.state
        count = self.delay if state == 8 else 0
        counting = int(state == 8)
        done = int(state == 9)
        if state == 0:
            self.state = 1 if data else 0
        elif state == 1:
            self.state = 2 if data else 0
        elif state == 2:
            self.state = 2 if data else 3
        elif state == 3:
            self.state = 4 if data else 0
        elif state < 8:
            self.state = state + 1
        elif state == 8:
            if self.fast == 999 and self.delay == 0:
                self.state = 9
        else:
            self.state = 0 if ack else 9
        if state >= 4 and state < 8:
            self.delay = ((self.delay << 1) | data) & 15
        elif state == 8 and self.fast == 999:
            self.delay = (self.delay - 1) & 15
        if state != 8 or self.fast == 999:
            self.fast = 0
        else:
            self.fast += 1
        return {"count": count, "counting": counting, "done": done}
