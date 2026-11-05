class LoadStoreUnit:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0
        self.address = 0
        self.write = 0
        self.lanes = 0
        self.data = 0
        self.result = 0
        self.pulse = 0

    def process(self, dmem_gnt_i, dmem_rvalid_i, dmem_rsp_rdata_i, ex_if_req_i, ex_if_we_i, ex_if_type_i, ex_if_wdata_i, ex_if_addr_base_i, ex_if_addr_offset_i):
        request = int(self.state == 1)
        out = {"dmem_req_o": request,
               "dmem_req_addr_o": self.address if request else 0,
               "dmem_req_we_o": self.write if request else 0,
               "dmem_req_be_o": self.lanes if request else 0,
               "dmem_req_wdata_o": self.data if request else 0,
               "ex_if_ready_o": int(self.state == 0),
               "wb_if_rdata_o": self.result, "wb_if_rvalid_o": self.pulse}
        self.pulse = 0
        if self.state == 0:
            address = (ex_if_addr_base_i + ex_if_addr_offset_i) & 0xFFFFFFFF
            lane = address & 3
            lanes = 0
            if ex_if_type_i == 0:
                lanes = 1 << lane
            elif ex_if_type_i == 1 and (lane & 1) == 0:
                lanes = 3 << lane
            elif ex_if_type_i == 2 and lane == 0:
                lanes = 15
            if ex_if_req_i and lanes:
                self.address = address
                self.write = ex_if_we_i
                self.data = ex_if_wdata_i if ex_if_we_i else 0
                self.lanes = lanes
                self.state = 1
        elif self.state == 1:
            if dmem_gnt_i:
                self.state = 0 if self.write else 2
        elif dmem_rvalid_i:
            self.result = dmem_rsp_rdata_i
            self.pulse = 1
            self.state = 0
        return out
