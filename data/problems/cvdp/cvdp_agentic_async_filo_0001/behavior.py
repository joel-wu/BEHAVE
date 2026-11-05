class Behavior:
    def __init__(self, DATA_WIDTH=16, DEPTH=8):
        self.DATA_WIDTH = DATA_WIDTH
        self.DEPTH = DEPTH
        self.reset()

    def reset(self):
        self.mem = [0] * self.DEPTH
        self.count = 0
        self.request = 0
        self.req1 = 0
        self.req2 = 0
        self.completed = 0
        self.done1 = 0
        self.done2 = 0
        self.reply = 0
        self.success = 0
        self.gray1 = 0
        self.gray2 = 0
        self.r_data = 0
        self.r_valid = 0
        self.r_busy = 0
        self.previous_wclk = 0
        self.previous_rclk = 0

    def process(self, w_clk_level, r_clk_level, w_reset_level, r_reset_level, push, pop, w_data):
        old_request = self.request
        old_completed = self.completed
        old_reply = self.reply
        old_success = self.success
        old_gray = self.count ^ (self.count >> 1)
        if w_reset_level or r_reset_level:
            self.count = 0
            self.request = 0
            self.req1 = 0
            self.req2 = 0
            self.completed = 0
            self.done1 = 0
            self.done2 = 0
            self.reply = 0
            self.success = 0
            self.gray1 = 0
            self.gray2 = 0
            self.r_data = 0
            self.r_valid = 0
            self.r_busy = 0
        else:
            if w_clk_level and not self.previous_wclk:
                n = self.count
                take_push = push and n < self.DEPTH
                if self.req2 != self.completed:
                    self.success = 1 if n > 0 else 0
                    self.reply = 0
                    if n > 0:
                        n = n - 1
                        self.reply = self.mem[n]
                    self.completed = self.req2
                if take_push:
                    self.mem[n] = w_data
                    n = n + 1
                self.count = n
                self.req2 = self.req1
                self.req1 = old_request
            if r_clk_level and not self.previous_rclk:
                self.r_valid = 0
                if self.r_busy:
                    if self.done2 == self.request:
                        self.r_busy = 0
                        self.r_valid = old_success
                        self.r_data = old_reply
                elif pop:
                    self.request = self.request ^ 1
                    self.r_busy = 1
                self.done2 = self.done1
                self.done1 = old_completed
                self.gray2 = self.gray1
                self.gray1 = old_gray
        self.previous_wclk = w_clk_level
        self.previous_rclk = r_clk_level
        return {"r_data": self.r_data, "r_empty": 1 if self.gray2 == 0 else 0,
                "w_full": 1 if self.count == self.DEPTH else 0,
                "r_busy": self.r_busy, "r_valid": self.r_valid}
