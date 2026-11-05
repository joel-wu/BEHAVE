class PerformanceCounter:
    def __init__(self, CNT_W=32):
        self.CNT_W = CNT_W
        self.reset()

    def reset(self):
        self.count = 0

    def process(self, sw_req_i, cpu_trig_i):
        p_count_o = self.count if sw_req_i else 0
        base = 0 if sw_req_i else self.count
        self.count = (base + cpu_trig_i) & ((1 << self.CNT_W) - 1)
        return {"p_count_o": p_count_o}
