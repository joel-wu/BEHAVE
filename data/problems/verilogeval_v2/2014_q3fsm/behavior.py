class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = 0
        self.phase = 0
        self.ones = 0
        self.z = 0

    def process(self, s, w):
        z = self.z
        self.z = 0
        if not self.active:
            if s:
                self.active = 1
        elif self.phase == 2:
            self.z = int(self.ones + w == 2)
            self.ones = 0
            self.phase = 0
        else:
            self.ones += w
            self.phase += 1
        return {"z": z}
