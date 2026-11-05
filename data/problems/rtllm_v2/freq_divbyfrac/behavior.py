class Behavior:
    def __init__(self, MUL2_DIV_CLK=7):
        self.MUL2_DIV_CLK = MUL2_DIV_CLK
        self.reset()

    def reset(self):
        self.previous_level = 0
        self.count = 0
        self.positive = 0
        self.negative = 0

    def process(self, clock_level, rst_n):
        if not rst_n:
            self.count = 0
            self.positive = 0
            self.negative = 0
        elif clock_level != self.previous_level:
            if clock_level:
                self.positive = int(self.count == 0 or self.count == self.MUL2_DIV_CLK // 2 + 1)
                self.count = (self.count + 1) % self.MUL2_DIV_CLK
            else:
                self.negative = int(self.count == 1 or self.count == self.MUL2_DIV_CLK // 2 + 1)
        self.previous_level = clock_level
        return {"clk_div": self.positive | self.negative}
