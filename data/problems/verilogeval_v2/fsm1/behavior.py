class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 1

    def process(self, value):
        state = self.state
        out = int(state == 1)
        if state == 0:
            self.state = 0 if value else 1
        elif state == 1:
            self.state = 1 if value else 0
        return {"out": out}
