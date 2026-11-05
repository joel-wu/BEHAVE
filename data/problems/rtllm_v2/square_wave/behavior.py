class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.wave = 0

    def process(self, freq):
        out = {"wave_out": self.wave}
        if freq != 0 and self.count == freq - 1:
            self.count = 0
            self.wave = self.wave ^ 1
        else:
            self.count = (self.count + 1) & 255
        return out
