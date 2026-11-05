class EvenClockDivider:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count_phase = 0
        self.clk_div_state = 0

    def process(self):
        level = self.clk_div_state
        if self.count_phase == 2:
            self.count_phase = 0
            self.clk_div_state = 1 - level
        else:
            self.count_phase = self.count_phase + 1
        return {"clk_div": level}
