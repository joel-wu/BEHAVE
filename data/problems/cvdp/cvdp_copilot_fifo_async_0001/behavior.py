class FifoAsync:
    def __init__(self, DATA_WIDTH=32, DEPTH=16):
        self.DATA_WIDTH = DATA_WIDTH
        self.DEPTH = DEPTH
        self.reset()

    def reset(self):
        self.mem = [0] * self.DEPTH
        self.wp = 0
        self.rp = 0
        self.rq1 = 0
        self.rq2 = 0
        self.wq1 = 0
        self.wq2 = 0

    def reset_w(self):
        self.mem = [0] * self.DEPTH
        self.wp = 0
        self.rq1 = 0
        self.rq2 = 0

    def reset_r(self):
        self.rp = 0
        self.wq1 = 0
        self.wq2 = 0

    def process_w(self, w_inc, w_data):
        full = int((self.wp ^ (self.wp >> 1)) ==
                   (self.rq2 ^ (self.DEPTH + self.DEPTH // 2)))
        self.rq2 = self.rq1
        self.rq1 = self.rp ^ (self.rp >> 1)
        if w_inc and not full:
            self.mem[self.wp & (self.DEPTH - 1)] = w_data & ((1 << self.DATA_WIDTH) - 1)
            self.wp = (self.wp + 1) & (2 * self.DEPTH - 1)
        return {"w_full": full}

    def process_r(self, r_inc):
        empty = int((self.rp ^ (self.rp >> 1)) == self.wq2)
        data = 0 if empty else self.mem[self.rp & (self.DEPTH - 1)]
        self.wq2 = self.wq1
        self.wq1 = self.wp ^ (self.wp >> 1)
        if r_inc and not empty:
            self.rp = (self.rp + 1) & (2 * self.DEPTH - 1)
        return {"r_empty": empty, "r_data": data}
