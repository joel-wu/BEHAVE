class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 1

    def process(self, train_valid, train_taken):
        state = self.q
        if train_valid:
            if train_taken:
                if self.q < 3:
                    self.q += 1
            elif self.q > 0:
                self.q -= 1
        return {"state": state}
