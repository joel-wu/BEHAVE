def signed32(value):
    value &= 0xffffffff
    return value - 0x100000000 if value & 0x80000000 else value


class SignalProcessingDspMathTrapezoidalInt:
    def __init__(self):
        self.reset()

    def reset(self):
        self.prev_x = 0
        self.prev_y = 0
        self.area_twice = 0
        self.have_prev = False

    def process(self, in_x, in_y):
        x = signed32(in_x)
        y = signed32(in_y)
        if not self.have_prev:
            self.prev_x = x
            self.prev_y = y
            self.have_prev = True
            return None
        contribution = (x - self.prev_x) * (y + self.prev_y)
        self.area_twice = (self.area_twice + contribution) & ((1 << 96) - 1)
        self.prev_x = x
        self.prev_y = y
        return {"out_sum": self.area_twice}
