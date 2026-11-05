class CvdpPrbsGen:
    def __init__(self, CHECK_MODE=0, POLY_LENGTH=31, POLY_TAP=3, WIDTH=16):
        self.CHECK_MODE = CHECK_MODE
        self.POLY_LENGTH = POLY_LENGTH
        self.POLY_TAP = POLY_TAP
        self.WIDTH = WIDTH
        self.reset()

    def reset(self):
        self.stages = (1 << self.POLY_LENGTH) - 1
        self.word = (1 << self.WIDTH) - 1

    def process(self, data_in):
        data_out = self.word
        stages = self.stages
        word = 0
        for i in range(self.WIDTH):
            feedback = ((stages >> (self.POLY_LENGTH - self.POLY_TAP)) ^ stages) & 1
            received = (data_in >> i) & 1
            output_bit = feedback ^ received if self.CHECK_MODE else feedback
            word |= output_bit << i
            inserted = received if self.CHECK_MODE else feedback
            stages = (stages >> 1) | (inserted << (self.POLY_LENGTH - 1))
        self.stages = stages
        self.word = word
        return {"data_out": data_out}
