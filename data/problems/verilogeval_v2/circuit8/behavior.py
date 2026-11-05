class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous_level = 0
        self.p = 0
        self.q = 0

    def process(self, clock_level, a):
        if clock_level:
            self.p = a
        elif self.previous_level:
            self.q = self.p
        self.previous_level = clock_level
        return {"p": self.p, "q": self.q}
