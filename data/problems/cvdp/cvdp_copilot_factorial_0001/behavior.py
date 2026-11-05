class Factorial:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = 0
        self.remaining = 0
        self.accumulator = 0
        self.fact = 0

    def process(self, num_in, start):
        busy = int(self.phase == 1)
        done = int(self.phase == 2)
        fact = self.fact
        if self.phase == 0:
            if start:
                self.phase = 1
                self.remaining = num_in
                self.accumulator = 1
        elif self.phase == 1:
            if self.remaining <= 1:
                self.fact = self.accumulator
                self.phase = 2
            else:
                self.accumulator = (self.accumulator * self.remaining) & ((1 << 64) - 1)
                self.remaining -= 1
        else:
            self.phase = 0
        return {"busy": busy, "fact": fact, "done": done}
