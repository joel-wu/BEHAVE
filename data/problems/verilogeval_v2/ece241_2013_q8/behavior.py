class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, x):
        state = self.state
        z = int(state == 2 and x == 1)
        if x:
            self.state = 1
        elif state == 1:
            self.state = 2
        else:
            self.state = 0
        return {"z": z}
