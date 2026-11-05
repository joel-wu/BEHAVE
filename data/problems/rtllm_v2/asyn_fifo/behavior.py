class Behavior:
    def __init__(self, WIDTH=8, DEPTH=16):
        self.WIDTH = WIDTH
        self.DEPTH = DEPTH
        self.reset()

    def reset(self):
        self.last_w = 0
        self.last_r = 0
        self.write_pos = 0
        self.read_pos = 0
        self.read_first = 0
        self.read_second = 0
        self.write_first = 0
        self.write_second = 0
        self.memory = [0] * self.DEPTH
        self.rdata = 0

    def process(self, wclk_level, rclk_level, wrstn, rrstn, winc, rinc, wdata):
        old_write = self.write_pos
        old_read = self.read_pos
        write_gray = old_write ^ (old_write >> 1)
        read_gray = old_read ^ (old_read >> 1)
        full_mask = self.DEPTH | (self.DEPTH >> 1)
        full = int(write_gray == (self.read_second ^ full_mask))
        empty = int(read_gray == self.write_second)
        write_edge = wclk_level and not self.last_w
        read_edge = rclk_level and not self.last_r
        if rrstn and read_edge and rinc and not empty:
            self.rdata = self.memory[old_read & (self.DEPTH - 1)]
        if wrstn and write_edge and winc and not full:
            self.memory[old_write & (self.DEPTH - 1)] = wdata
        if not wrstn:
            self.write_pos = 0
            self.read_first = 0
            self.read_second = 0
        elif write_edge:
            self.read_second = self.read_first
            self.read_first = read_gray
            if winc and not full:
                self.write_pos = (old_write + 1) & (2 * self.DEPTH - 1)
        if not rrstn:
            self.read_pos = 0
            self.write_first = 0
            self.write_second = 0
        elif read_edge:
            self.write_second = self.write_first
            self.write_first = write_gray
            if rinc and not empty:
                self.read_pos = (old_read + 1) & (2 * self.DEPTH - 1)
        self.last_w = wclk_level
        self.last_r = rclk_level
        new_write_gray = self.write_pos ^ (self.write_pos >> 1)
        new_read_gray = self.read_pos ^ (self.read_pos >> 1)
        return {"wfull": int(new_write_gray == (self.read_second ^ full_mask)), "rempty": int(new_read_gray == self.write_second), "rdata": self.rdata}
