class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pm = 0
        self.hour = 12
        self.minute = 0
        self.second = 0

    def process(self, ena):
        pm = self.pm
        hh = (self.hour // 10) * 16 + self.hour % 10
        mm = (self.minute // 10) * 16 + self.minute % 10
        ss = (self.second // 10) * 16 + self.second % 10
        if ena:
            if self.second < 59:
                self.second += 1
            else:
                self.second = 0
                if self.minute < 59:
                    self.minute += 1
                else:
                    self.minute = 0
                    if self.hour == 11:
                        self.pm = 1 - self.pm
                    self.hour = 1 if self.hour == 12 else self.hour + 1
        return {"pm": pm, "hh": hh, "mm": mm, "ss": ss}
