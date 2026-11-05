class CicDecimator:
    def __init__(self, WIDTH=16, RMAX=2, M=1, N=2, REG_WIDTH=0):
        self.WIDTH = WIDTH
        self.RMAX = RMAX
        self.M = M
        self.N = N
        self.REG_WIDTH = REG_WIDTH
        if self.REG_WIDTH == 0:
            self.REG_WIDTH = WIDTH + ((RMAX*M)**N-1).bit_length()
        self.reset()

    def reset(self):
        self.integrators = [0] * self.N
        self.delays = [0] * (self.N * self.M)
        self.phase = 0

    def process(self, input_tdata, input_tvalid, output_tready, rate):
        mask = (1 << self.REG_WIDTH) - 1
        take_output = bool(input_tvalid and self.phase == 0 and output_tready)
        ready = int(bool(output_tready or self.phase != 0))
        valid = int(bool(input_tvalid and self.phase == 0))
        value = self.integrators[self.N-1]
        for stage in range(self.N):
            base = stage * self.M
            prior = self.delays[base+self.M-1]
            if take_output:
                for lag in range(self.M-1, 0, -1):
                    self.delays[base+lag] = self.delays[base+lag-1]
                self.delays[base] = value
            value = (value-prior) & mask
        out = {"input_tready": ready, "output_tdata": value, "output_tvalid": valid}
        if input_tvalid and ready:
            for stage in range(self.N-1, 0, -1):
                self.integrators[stage] = (self.integrators[stage]+self.integrators[stage-1]) & mask
            sample = input_tdata
            if sample >= (1 << (self.WIDTH-1)):
                sample -= 1 << self.WIDTH
            self.integrators[0] = (self.integrators[0]+sample) & mask
            selected = max(1, min(self.RMAX, rate))
            self.phase = 0 if self.phase >= selected-1 else self.phase+1
        return out
