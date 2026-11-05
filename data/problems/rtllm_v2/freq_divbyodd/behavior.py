class Behavior:
    def __init__(self, NUM_DIV=5):
        self.NUM_DIV = NUM_DIV
        self.reset()

    def reset(self):
        self.previous_level = 0
        self.rising_count = 0
        self.falling_count = 0
        self.positive = 1
        self.negative = 1

    def process(self, clock_level, rst_n):
        if not rst_n:
            self.rising_count = 0
            self.falling_count = 0
            self.positive = 1
            self.negative = 1
        elif clock_level != self.previous_level:
            if clock_level:
                self.positive = int(self.rising_count < self.NUM_DIV // 2)
                self.rising_count = (self.rising_count + 1) % self.NUM_DIV
            else:
                self.negative = int(self.falling_count < self.NUM_DIV // 2)
                self.falling_count = (self.falling_count + 1) % self.NUM_DIV
        self.previous_level = clock_level
        return {"clk_div": self.positive | self.negative}
