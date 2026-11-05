class EdgeDetect:
    def __init__(self):
        self.reset()

    def reset(self):
        self.prev_a = 0
        self.rise = 0
        self.down = 0

    def process(self, a):
        result = {"rise": self.rise, "down": self.down}
        a = a & 1
        self.rise = a & (self.prev_a ^ 1)
        self.down = (a ^ 1) & self.prev_a
        self.prev_a = a
        return result
