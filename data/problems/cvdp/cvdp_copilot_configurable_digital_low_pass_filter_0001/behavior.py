class LowPassFilter:
    def __init__(self, DATA_WIDTH=16, COEFF_WIDTH=16, NUM_TAPS=8):
        self.DATA_WIDTH = DATA_WIDTH
        self.COEFF_WIDTH = COEFF_WIDTH
        self.NUM_TAPS = NUM_TAPS
        self.SUM_WIDTH = DATA_WIDTH + COEFF_WIDTH + (NUM_TAPS-1).bit_length()
        self.reset()

    def reset(self):
        self.data = 0
        self.coefficients = 0
        self.valid = 0

    def process(self, valid_in, data_in, coeffs):
        w = self.DATA_WIDTH
        c = self.COEFF_WIDTH
        total = 0
        for i in range(self.NUM_TAPS):
            a = (self.data >> (i*w)) & ((1 << w)-1)
            b = (self.coefficients >> ((self.NUM_TAPS-1-i)*c)) & ((1 << c)-1)
            a = a - (1 << w) if a & (1 << (w-1)) else a
            b = b - (1 << c) if b & (1 << (c-1)) else b
            total += a*b
        old_valid = self.valid
        if valid_in:
            self.data = data_in
            self.coefficients = coeffs
        self.valid = valid_in
        return {"valid_out":old_valid, "data_out":total & ((1 << self.SUM_WIDTH)-1)}
