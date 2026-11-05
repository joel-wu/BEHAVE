class Behavior:
    def __init__(self, p_data_width=32, p_addr_width=16):
        self.p_data_width = p_data_width
        self.p_addr_width = p_addr_width
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
        self.full = 0
        self.empty = 1
        self.memory = [0] * (1 << self.p_addr_width)
        self.rdata = 0

    def process(self, i_wr_clk_level, i_rd_clk_level, i_wr_rst_n, i_rd_rst_n, i_wr_en, i_rd_en, i_wr_data):
        depth = 1 << self.p_addr_width
        old_write = self.write_pos
        old_read = self.read_pos
        write_gray = old_write ^ (old_write >> 1)
        read_gray = old_read ^ (old_read >> 1)
        write_edge = i_wr_clk_level and not self.last_w
        read_edge = i_rd_clk_level and not self.last_r
        push = i_wr_en and not self.full
        pop = i_rd_en and not self.empty
        if i_rd_rst_n and read_edge and pop:
            self.rdata = self.memory[old_read & (depth-1)]
        if i_wr_rst_n and write_edge and push:
            self.memory[old_write & (depth-1)] = i_wr_data
        if not i_wr_rst_n:
            self.write_pos = 0
            self.read_first = 0
            self.read_second = 0
            self.full = 0
        elif write_edge:
            if push:
                self.write_pos = (old_write+1) & (2*depth-1)
            gray = self.write_pos ^ (self.write_pos >> 1)
            self.full = int(gray == (self.read_second ^ (depth | (depth >> 1))))
            self.read_second = self.read_first
            self.read_first = read_gray
        if not i_rd_rst_n:
            self.read_pos = 0
            self.write_first = 0
            self.write_second = 0
            self.empty = 1
        elif read_edge:
            if pop:
                self.read_pos = (old_read+1) & (2*depth-1)
            gray = self.read_pos ^ (self.read_pos >> 1)
            self.empty = int(gray == self.write_second)
            self.write_second = self.write_first
            self.write_first = write_gray
        self.last_w = i_wr_clk_level
        self.last_r = i_rd_clk_level
        return {"o_fifo_full": self.full, "o_fifo_empty": self.empty, "o_rd_data": self.rdata}
