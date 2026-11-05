class PerfectSquaresGenerator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.square = 1
        self.odd_increment = 3

    def process(self):
        sqr_o = self.square
        if self.square != 0xFFFFFFFF:
            candidate = self.square + self.odd_increment
            self.square = 0xFFFFFFFF if candidate >= (1 << 32) else candidate
            self.odd_increment += 2
        return {"sqr_o": sqr_o}
