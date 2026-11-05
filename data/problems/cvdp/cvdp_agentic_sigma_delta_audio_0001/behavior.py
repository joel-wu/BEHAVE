class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous_clock = 0
        self.phase = 0
        self.l_sample = 0
        self.r_sample = 0
        self.l_target = 0
        self.r_target = 0
        self.l_slope = 0
        self.r_slope = 0
        self.l_ac1 = 0
        self.r_ac1 = 0
        self.l_ac2 = 0
        self.r_ac2 = 0
        self.l_feedback = 0
        self.r_feedback = 0
        self.seed_1 = 1
        self.seed_2 = 2
        self.left = 0
        self.right = 0

    def process(self, clk_sig_level, clk_en_sig, load_data_sum, read_data_sum):
        if clk_sig_level and not self.previous_clock and clk_en_sig:
            if self.phase == 0:
                self.l_target = load_data_sum - 32768 if load_data_sum & 16384 else load_data_sum
                self.r_target = read_data_sum - 32768 if read_data_sum & 16384 else read_data_sum
                self.l_slope = (self.l_target - self.l_sample) >> 2
                self.r_slope = (self.r_target - self.r_sample) >> 2
            if self.phase == 3:
                self.l_sample = self.l_target
                self.r_sample = self.r_target
            else:
                self.l_sample = self.l_sample + self.l_slope
                self.r_sample = self.r_sample + self.r_slope
            noise = 1 if (self.seed_1 ^ self.seed_2) & 1 else -1
            l1 = (self.l_ac1 + self.l_sample - self.l_feedback) & 131071
            r1 = (self.r_ac1 + self.r_sample - self.r_feedback) & 131071
            self.l_ac1 = l1 - 131072 if l1 & 65536 else l1
            self.r_ac1 = r1 - 131072 if r1 & 65536 else r1
            l2 = (self.l_ac2 + self.l_ac1 - self.l_feedback + noise) & 1048575
            r2 = (self.r_ac2 + self.r_ac1 - self.r_feedback - noise) & 1048575
            self.l_ac2 = l2 - 1048576 if l2 & 524288 else l2
            self.r_ac2 = r2 - 1048576 if r2 & 524288 else r2
            self.left = 1 if self.l_ac2 >= 0 else 0
            self.right = 1 if self.r_ac2 >= 0 else 0
            self.l_feedback = 16383 if self.left else -16384
            self.r_feedback = 16383 if self.right else -16384
            s1 = ((self.seed_1 >> 15) ^ (self.seed_1 >> 13) ^ (self.seed_1 >> 12) ^ (self.seed_1 >> 10)) & 1
            s2 = ((self.seed_2 >> 15) ^ (self.seed_2 >> 14) ^ (self.seed_2 >> 12) ^ (self.seed_2 >> 3)) & 1
            self.seed_1 = ((self.seed_1 << 1) | s1) & 65535
            self.seed_2 = ((self.seed_2 << 1) | s2) & 65535
            self.phase = (self.phase + 1) & 3
        self.previous_clock = clk_sig_level
        return {"left_sig": self.left, "right_sig": self.right}
