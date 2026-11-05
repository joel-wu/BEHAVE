class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, w):
        state = self.state
        z = int(state == 4 or state == 5)
        if state == 0:
            self.state = 0 if w else 1
        elif state == 1:
            self.state = 3 if w else 2
        elif state == 2:
            self.state = 3 if w else 4
        elif state == 3:
            self.state = 0 if w else 5
        elif state == 4:
            self.state = 3 if w else 4
        elif state == 5:
            self.state = 3 if w else 2
        return {"z": z}
