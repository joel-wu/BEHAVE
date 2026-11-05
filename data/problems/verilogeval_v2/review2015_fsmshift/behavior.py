class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.remaining = 4

    def process(self):
        shift_ena = int(self.remaining > 0)
        if self.remaining:
            self.remaining -= 1
        return {"shift_ena": shift_ena}
