class DigStopwatch:
    def __init__(self, CLK_FREQ=50000000):
        self.CLK_FREQ = CLK_FREQ
        self.reset()

    def reset(self):
        self.cycles = 0
        self.elapsed = 0

    def process(self, start_stop):
        hour = 1 if self.elapsed == 3600 else 0
        minute = (self.elapsed // 60) % 60
        second = self.elapsed % 60
        if start_stop and self.elapsed < 3600:
            if self.cycles == self.CLK_FREQ-1:
                self.cycles = 0
                self.elapsed += 1
            else:
                self.cycles += 1
        return {"seconds":second, "minutes":minute, "hour":hour}
