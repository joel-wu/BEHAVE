class DecimatorAndPeakDetector:
    def __init__(self, N=8, DATA_WIDTH=16, DEC_FACTOR=4):
        self.N = N
        self.DATA_WIDTH = DATA_WIDTH
        self.DEC_FACTOR = DEC_FACTOR
        self.M = N // DEC_FACTOR
        self.reset()

    def reset(self):
        self.frame = 0
        self.valid = 0

    def process(self, valid_in, data_in):
        w = self.DATA_WIDTH
        mask = (1 << w) - 1
        data = 0
        peak = -(1 << (w-1))
        for j in range(self.M):
            bits = (self.frame >> (w*(self.N-1-j*self.DEC_FACTOR))) & mask
            signed = bits - (1 << w) if bits & (1 << (w-1)) else bits
            if signed > peak:
                peak = signed
            data = (data << w) | bits
        old_valid = self.valid
        self.frame = data_in
        self.valid = valid_in
        return {"valid_out":old_valid, "data_out":data, "peak_value":peak & mask}
