class PidStateResetF32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mem = [0] * 16

    def process(self, in_base):
        if in_base <= 13:
            for offset in range(3):
                self.mem[in_base + offset] = 0
            return {"out_done": 1}
        return {"out_done": 0}
