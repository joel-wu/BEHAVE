class BcdCounter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.time = 0

    def process(self):
        hour = self.time // 3600
        minute = (self.time // 60) % 60
        second = self.time % 60
        self.time = 0 if self.time == 86399 else self.time + 1
        return {"ms_hr":hour // 10, "ls_hr":hour % 10,
                "ms_min":minute // 10, "ls_min":minute % 10,
                "ms_sec":second // 10, "ls_sec":second % 10}
