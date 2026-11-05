class FrequencyDivider:
    def __init__(self):
        self.reset()

    def reset(self):
        self.clk_50_state = 0
        self.clk_10_state = 0
        self.clk_1_state = 0
        self.cnt_10 = 0
        self.cnt_100 = 0

    def process(self):
        clk_50 = self.clk_50_state
        clk_10 = self.clk_10_state
        clk_1 = self.clk_1_state
        self.clk_50_state = 1 - clk_50
        if self.cnt_10 == 4:
            self.cnt_10 = 0
            self.clk_10_state = 1 - clk_10
        else:
            self.cnt_10 = self.cnt_10 + 1
        if self.cnt_100 == 49:
            self.cnt_100 = 0
            self.clk_1_state = 1 - clk_1
        else:
            self.cnt_100 = self.cnt_100 + 1
        return {"CLK_50": clk_50, "CLK_10": clk_10, "CLK_1": clk_1}
