class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_clk1 = 0
        self.last_clk2 = 0
        self.enable1 = 0
        self.enable2 = 0

    def process(self, clk1_level, clk2_level, sel, rst_n):
        old1 = self.enable1
        old2 = self.enable2
        if not rst_n:
            self.enable1 = 0
            self.enable2 = 0
        else:
            if self.last_clk1 and not clk1_level:
                self.enable1 = int(not sel and not old2)
            if self.last_clk2 and not clk2_level:
                self.enable2 = int(sel and not old1)
        self.last_clk1 = clk1_level
        self.last_clk2 = clk2_level
        return {"clkout": (clk1_level & self.enable1) | (clk2_level & self.enable2)}
