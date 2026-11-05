class ClockDivider:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0

    def process(self, sel):
        value = (self.count >> sel) & 1 if sel < 3 else 0
        self.count = (self.count + 1) & 7
        return {"clk_out":value}
