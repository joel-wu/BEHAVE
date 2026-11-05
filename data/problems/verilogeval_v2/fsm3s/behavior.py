class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, value):
        state = self.state
        out = int(state == 3)
        if state == 0:
            self.state = 1 if value else 0
        elif state == 1:
            self.state = 1 if value else 2
        elif state == 2:
            self.state = 3 if value else 0
        elif state == 3:
            self.state = 1 if value else 2
        return {"out": out}
