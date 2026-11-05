class SignalGenerator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.wave = 0
        self.direction = 0

    def process(self):
        wave = self.wave
        if self.direction == 0:
            if wave == 31:
                self.direction = 1
            else:
                self.wave = wave + 1
        else:
            if wave == 0:
                self.direction = 0
            else:
                self.wave = wave - 1
        return {"wave": wave}
