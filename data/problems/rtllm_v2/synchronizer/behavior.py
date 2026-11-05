class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_a = 0
        self.last_b = 0
        self.source_data = 0
        self.source_enable = 0
        self.enable_first = 0
        self.enable_second = 0
        self.dataout = 0

    def process(self, clk_a_level, clk_b_level, arstn, brstn, data_in, data_en):
        old_data = self.source_data
        old_enable = self.source_enable
        if not arstn:
            self.source_data = 0
            self.source_enable = 0
        elif clk_a_level and not self.last_a:
            self.source_data = data_in
            self.source_enable = data_en
        if not brstn:
            self.enable_first = 0
            self.enable_second = 0
            self.dataout = 0
        elif clk_b_level and not self.last_b:
            if self.enable_second:
                self.dataout = old_data
            self.enable_second = self.enable_first
            self.enable_first = old_enable
        self.last_a = clk_a_level
        self.last_b = clk_b_level
        return {"dataout": self.dataout}
