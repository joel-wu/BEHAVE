class ReferenceClockGenerator:
    def __init__(self, PERIOD=10):
        self.PERIOD = PERIOD
        self.reset()

    def reset(self):
        self.phase = 0
        self.level = 0

    def process(self):
        result = self.level
        if self.phase == self.PERIOD // 2 - 1:
            self.phase = 0
            self.level = 1 - self.level
        else:
            self.phase = self.phase + 1
        return {"clk_out": result}
