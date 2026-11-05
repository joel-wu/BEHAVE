class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.wb_state = 0
        self.request = 0
        self.done1 = 0
        self.done2 = 0
        self.req_size = 0
        self.req_addr = 0
        self.req_data = 0
        self.req_mask = 0
        self.req_write = 0
        self.ahb_state = 0
        self.req1 = 0
        self.req2 = 0
        self.completed = 0
        self.response_data = 0
        self.response_error = 0
        self.data_o = 0
        self.ack_o = 0
        self.err_o = 0
        self.hsize = 0
        self.hwrite = 0
        self.haddr = 0
        self.hwdata = 0
        self.previous_wclk = 0
        self.previous_hclk = 0

    def process(self, clk_i_level, rst_i, cyc_i, stb_i, sel_i, we_i, addr_i, data_i, hclk_level, hreset_n, hrdata, hresp, hready):
        old_completed = self.completed
        old_response_data = self.response_data
        old_response_error = self.response_error
        if not rst_i or not hreset_n:
            self.wb_state = 0
            self.request = 0
            self.done1 = 0
            self.done2 = 0
            self.req_size = 0
            self.req_addr = 0
            self.req_data = 0
            self.req_mask = 0
            self.req_write = 0
            self.ahb_state = 0
            self.req1 = 0
            self.req2 = 0
            self.completed = 0
            self.response_data = 0
            self.response_error = 0
            self.data_o = 0
            self.ack_o = 0
            self.err_o = 0
            self.hsize = 0
            self.hwrite = 0
            self.haddr = 0
            self.hwdata = 0
        else:
            if hclk_level and not self.previous_hclk:
                if self.ahb_state == 0:
                    if self.req2 != self.completed:
                        self.hsize = self.req_size
                        self.haddr = self.req_addr
                        self.hwdata = self.req_data
                        self.hwrite = self.req_write
                        self.ahb_state = 1
                elif self.ahb_state == 1:
                    if hready:
                        self.ahb_state = 2
                elif hready:
                    reversed_read = ((hrdata & 255) << 24) | ((hrdata & 65280) << 8) | ((hrdata >> 8) & 65280) | ((hrdata >> 24) & 255)
                    self.response_data = 0 if hresp or self.req_write else reversed_read & self.req_mask
                    self.response_error = 1 if hresp else 0
                    self.completed = self.req2
                    self.ahb_state = 0
                self.req2 = self.req1
                self.req1 = self.request
            if clk_i_level and not self.previous_wclk:
                self.ack_o = 0
                self.err_o = 0
                if self.wb_state == 2:
                    self.wb_state = 0
                elif self.wb_state == 1:
                    if self.done2 == self.request:
                        self.data_o = old_response_data
                        if cyc_i and stb_i:
                            self.ack_o = 0 if old_response_error else 1
                            self.err_o = old_response_error
                        self.wb_state = 2
                elif cyc_i and stb_i:
                    valid = 1
                    size = 0
                    offset = 0
                    mask = 0
                    if sel_i == 1:
                        mask = 255
                    elif sel_i == 2:
                        mask = 65280
                        offset = 1
                    elif sel_i == 4:
                        mask = 16711680
                        offset = 2
                    elif sel_i == 8:
                        mask = 4278190080
                        offset = 3
                    elif sel_i == 3:
                        mask = 65535
                        size = 1
                    elif sel_i == 12:
                        mask = 4294901760
                        size = 1
                        offset = 2
                    elif sel_i == 15:
                        mask = 4294967295
                        size = 2
                    else:
                        valid = 0
                    if valid:
                        masked = data_i & mask
                        self.req_data = ((masked & 255) << 24) | ((masked & 65280) << 8) | ((masked >> 8) & 65280) | ((masked >> 24) & 255)
                        self.req_mask = mask
                        self.req_size = size
                        self.req_addr = (addr_i & 4294967292) | offset
                        self.req_write = we_i
                        self.request = self.request ^ 1
                        self.wb_state = 1
                    else:
                        self.data_o = 0
                        self.err_o = 1
                        self.wb_state = 2
                self.done2 = self.done1
                self.done1 = old_completed
        self.previous_wclk = clk_i_level
        self.previous_hclk = hclk_level
        return {"data_o": self.data_o, "ack_o": self.ack_o, "err_o": self.err_o,
                "htrans": 2 if self.ahb_state == 1 else 0, "hsize": self.hsize,
                "hburst": 0, "hwrite": self.hwrite, "haddr": self.haddr, "hwdata": self.hwdata}
