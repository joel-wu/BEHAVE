class PerceptronGates:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pc = 0
        self.index = 0
        self.w1 = 0
        self.w2 = 0
        self.bias = 0
        self.xa = 0
        self.xb = 0
        self.rate = 0
        self.gate = 0
        self.target = 0
        self.y_in = 0
        self.y = 0
        self.d1 = 0
        self.d2 = 0
        self.db = 0
        self.epoch_changed = 0
        self.stopped = 0

    def process(self, x1, x2, learning_rate, threshold, gate_select):
        out = {
               "percep_w1": self.w1 - 16 if self.w1 >= 8 else self.w1,
               "percep_w2": self.w2 - 16 if self.w2 >= 8 else self.w2,
               "percep_bias": self.bias - 16 if self.bias >= 8 else self.bias,
               "present_addr": self.pc, "stop": self.stopped, "input_index": self.index,
               "y_in": self.y_in - 16 if self.y_in >= 8 else self.y_in,
               "y": self.y - 16 if self.y >= 8 else self.y,
               "prev_percep_wt_1": self.d1 - 16 if self.d1 >= 8 else self.d1,
               "prev_percep_wt_2": self.d2 - 16 if self.d2 >= 8 else self.d2,
               "prev_percep_bias": self.db - 16 if self.db >= 8 else self.db}
        if not self.stopped:
            if self.pc == 0:
                self.w1 = 0
                self.w2 = 0
                self.bias = 0
                self.index = 0
                self.d1 = 0
                self.d2 = 0
                self.db = 0
                self.epoch_changed = 0
                self.pc = 1
            elif self.pc == 1:
                self.xa = x1
                self.xb = x2
                self.rate = learning_rate
                self.gate = gate_select
                wa = self.w1 - 16 if self.w1 >= 8 else self.w1
                wb = self.w2 - 16 if self.w2 >= 8 else self.w2
                bias_value = self.bias - 16 if self.bias >= 8 else self.bias
                total = bias_value + self.xa * wa + self.xb * wb
                self.y_in = total & 15
                self.y = 1 if total > threshold else (15 if total < threshold else 0)
                self.pc = 2
            elif self.pc == 2:
                positive = self.index == 0
                if self.gate == 1:
                    positive = self.index != 3
                elif self.gate == 2:
                    positive = self.index != 0
                elif self.gate == 3:
                    positive = self.index == 3
                self.target = 1 if positive else -1
                self.pc = 3
            elif self.pc == 3:
                da = 0
                db = 0
                dc = 0
                if self.y != (self.target & 15) and self.rate:
                    da = self.xa * self.target
                    db = self.xb * self.target
                    dc = self.target
                self.d1 = da & 15
                self.d2 = db & 15
                self.db = dc & 15
                self.w1 = (self.w1 + da) & 15
                self.w2 = (self.w2 + db) & 15
                self.bias = (self.bias + dc) & 15
                self.epoch_changed = int(bool(self.epoch_changed or da or db or dc))
                self.pc = 4
            elif self.pc == 4:
                if self.index == 3 and not self.epoch_changed:
                    self.stopped = 1
                self.pc = 5
            elif self.pc == 5:
                if self.index == 3:
                    self.epoch_changed = 0
                self.index = (self.index + 1) & 3
                self.pc = 1
        return out
