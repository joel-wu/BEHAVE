class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, w):
        state = self.state
        z = int(state == 4 or state == 5)
        if state == 0:
            self.state = 1 if w else 0
        elif state == 1:
            self.state = 2 if w else 3
        elif state == 2:
            self.state = 4 if w else 3
        elif state == 3:
            self.state = 5 if w else 0
        elif state == 4:
            self.state = 4 if w else 3
        elif state == 5:
            self.state = 2 if w else 3
        return {"z": z}
