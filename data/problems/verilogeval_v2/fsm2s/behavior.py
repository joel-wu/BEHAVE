class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, j, k):
        out = self.state
        if self.state == 0:
            self.state = j
        else:
            self.state = 0 if k else 1
        return {"out": out}
