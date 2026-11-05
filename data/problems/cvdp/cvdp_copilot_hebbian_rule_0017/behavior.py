class HebbianRule:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0
        self.index = 0
        self.gate = 0
        self.w1 = 0
        self.w2 = 0
        self.bias = 0
        self.x1 = 0
        self.x2 = 0
        self.target = 0
        self.d1 = 0
        self.d2 = 0
        self.db = 0

    def process(self, start, a, b, gate_select):
        next_state = 0
        if self.state == 0:
            next_state = int(bool(start))
        elif self.state == 2:
            next_state = 3 + self.gate
        elif self.state >= 3 and self.state <= 6:
            next_state = 7
        elif self.state == 9:
            next_state = 1 if self.index < 3 else 10
        elif self.state != 10:
            next_state = self.state + 1
        out = {"w1": self.w1 - 16 if self.w1 >= 8 else self.w1,
               "w2": self.w2 - 16 if self.w2 >= 8 else self.w2,
               "bias": self.bias - 16 if self.bias >= 8 else self.bias,
               "present_state": self.state, "next_state": next_state}
        if self.state == 0 and start:
            self.w1 = 0
            self.w2 = 0
            self.bias = 0
            self.index = 0
            self.gate = gate_select
        elif self.state == 1:
            self.x1 = a
            self.x2 = b
        elif self.state == 3:
            self.target = 1 if self.x1 == 1 and self.x2 == 1 else -1
        elif self.state == 4:
            self.target = 1 if self.x1 == 1 or self.x2 == 1 else -1
        elif self.state == 5:
            self.target = -1 if self.x1 == 1 and self.x2 == 1 else 1
        elif self.state == 6:
            self.target = -1 if self.x1 == 1 or self.x2 == 1 else 1
        elif self.state == 7:
            self.d1 = self.x1 * self.target
            self.d2 = self.x2 * self.target
            self.db = self.target
        elif self.state == 8:
            self.w1 = (self.w1 + self.d1) & 15
            self.w2 = (self.w2 + self.d2) & 15
            self.bias = (self.bias + self.db) & 15
        elif self.state == 9 and self.index < 3:
            self.index += 1
        self.state = next_state
        return out
