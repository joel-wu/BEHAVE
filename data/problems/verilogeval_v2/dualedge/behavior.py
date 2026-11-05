class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous_level = 0
        self.positive = 0
        self.negative = 0

    def process(self, clock_level, d):
        if clock_level != self.previous_level:
            if clock_level:
                self.positive = d
            else:
                self.negative = d
        self.previous_level = clock_level
        return {"q": self.positive if clock_level else self.negative}
