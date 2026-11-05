class Crossbar3x2:
    def __init__(self):
        self.reset()

    def reset(self):
        self.valid = [0, 0]
        self.data = [0, 0]
        self.origin = [0, 0]
        self.priority = [0, 0]

    def process(self, s0_valid, s0_dest, s0_data, s1_valid, s1_dest, s1_data,
                s2_valid, s2_dest, s2_data, m0_ready, m1_ready):
        valids = [s0_valid, s1_valid, s2_valid]
        destinations = [s0_dest, s1_dest, s2_dest]
        payloads = [s0_data, s1_data, s2_data]
        ready = [m0_ready, m1_ready]
        selected = [-1, -1]
        source_ready = [0, 0, 0]
        for m in range(2):
            if not self.valid[m] or ready[m]:
                for delta in range(3):
                    s = self.priority[m] + delta
                    if s >= 3:
                        s = s - 3
                    if selected[m] == -1 and valids[s] and destinations[s] == m:
                        selected[m] = s
                        source_ready[s] = 1
        result = {
            "s0_ready": source_ready[0], "s1_ready": source_ready[1], "s2_ready": source_ready[2],
            "m0_valid": self.valid[0], "m0_data": self.data[0] if self.valid[0] else 0,
            "m0_source": self.origin[0] if self.valid[0] else 0,
            "m1_valid": self.valid[1], "m1_data": self.data[1] if self.valid[1] else 0,
            "m1_source": self.origin[1] if self.valid[1] else 0,
        }
        for m in range(2):
            if ready[m]:
                self.valid[m] = 0
            s = selected[m]
            if s >= 0:
                self.valid[m] = 1
                self.data[m] = payloads[s]
                self.origin[m] = s
                self.priority[m] = 0 if s == 2 else s + 1
        return result
