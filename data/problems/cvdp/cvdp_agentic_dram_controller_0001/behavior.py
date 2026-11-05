class Behavior:
    def __init__(self, del_width=15, len_auto_ref=10, len_small=8, addr_bits_to_dram=13, addr_bits_from_up=24, ba_bits=2):
        self.del_width = del_width
        self.len_auto_ref = len_auto_ref
        self.len_small = len_small
        self.addr_bits_to_dram = addr_bits_to_dram
        self.addr_bits_from_up = addr_bits_from_up
        self.ba_bits = ba_bits
        self.reset()

    def reset(self):
        self.init_tick = 0
        self.initialized = 0
        self.refresh_tick = 0
        self.pending = 0
        self.phase = 0
        self.age = 0
        self.req_write = 0
        self.req_col = 0
        self.terminate = 0
        self.prev_active = 0
        self.command = 63
        self.addr = 0
        self.ba = 0
        self.cke = 0
        self.rd_ready = 0
        self.previous_clock = 0

    def process(self, clk_in_level, reset_level, addr_from_up, rd_n_from_up, wr_n_from_up, bus_term_from_up):
        if clk_in_level and not self.previous_clock:
            if reset_level:
                self.init_tick = 0
                self.initialized = 0
                self.refresh_tick = 0
                self.pending = 0
                self.phase = 0
                self.age = 0
                self.req_write = 0
                self.req_col = 0
                self.terminate = 0
                self.prev_active = 0
                self.command = 63
                self.addr = 0
                self.ba = 0
                self.cke = 0
                self.rd_ready = 0
            else:
                active = 1 if not rd_n_from_up or not wr_n_from_up else 0
                new_request = active and not self.prev_active
                self.prev_active = active
                self.cke = 1
                self.command = 31
                if not self.initialized:
                    if self.init_tick == 10000:
                        self.command = 11
                        self.addr = 1024
                    elif self.init_tick == 10004 or self.init_tick == 10012:
                        self.command = 7
                    elif self.init_tick == 10020:
                        self.command = 3
                        self.addr = 39
                    elif self.init_tick == 10023:
                        self.initialized = 1
                    self.init_tick = min(self.init_tick + 1, 10024)
                else:
                    due = 1 if self.refresh_tick == 780 else 0
                    self.refresh_tick = 0 if due else self.refresh_tick + 1
                    complete = 1 if self.phase == 5 and self.age == 7 else 0
                    self.pending = min((1 << self.len_auto_ref) - 1, max(0, self.pending + due - complete))
                    if self.phase == 0:
                        if self.pending:
                            self.command = 7
                            self.phase = 5
                            self.age = 0
                        elif new_request and bus_term_from_up:
                            columns = self.addr_bits_from_up - self.addr_bits_to_dram - self.ba_bits
                            self.addr = (addr_from_up >> columns) & ((1 << self.addr_bits_to_dram) - 1)
                            self.ba = (addr_from_up >> (columns + self.addr_bits_to_dram)) & ((1 << self.ba_bits) - 1)
                            self.req_col = addr_from_up & ((1 << columns) - 1)
                            self.req_write = 1 if not wr_n_from_up else 0
                            self.terminate = 0
                            self.command = 15
                            self.phase = 1
                            self.age = 0
                    elif self.phase == 1:
                        if not active or not bus_term_from_up:
                            self.terminate = 1
                        if self.age == 1:
                            self.command = 16 if self.req_write else 20
                            self.addr = self.req_col
                            self.phase = 2
                            self.age = 0
                        else:
                            self.age = self.age + 1
                    elif self.phase == 2:
                        if self.terminate or not active or not bus_term_from_up:
                            self.command = 27
                            self.rd_ready = 0
                            self.phase = 3
                        else:
                            self.command = 28
                            if self.age >= 1 and not self.req_write:
                                self.rd_ready = 1
                            self.age = min(self.age + 1, 2)
                    elif self.phase == 3:
                        self.command = 11
                        self.addr = 0
                        self.phase = 4
                        self.age = 0
                    elif self.phase == 4:
                        if self.age == 3:
                            self.phase = 0
                        else:
                            self.age = self.age + 1
                    elif self.phase == 5:
                        if complete:
                            self.phase = 0
                        else:
                            self.age = self.age + 1
        self.previous_clock = clk_in_level
        return {"addr": self.addr, "ba": self.ba, "clk": clk_in_level, "cke": self.cke,
                "cs_n": (self.command >> 5) & 1, "ras_n": (self.command >> 4) & 1,
                "cas_n": (self.command >> 3) & 1, "we_n": (self.command >> 2) & 1,
                "dqm": self.command & 3, "dram_init_done": self.initialized,
                "dram_busy": 1 if not self.initialized or self.phase or self.pending else 0,
                "rd_dat_from_dram_ready": self.rd_ready}
