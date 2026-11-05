class PrecisionCounterAxi:
    def __init__(self, C_S_AXI_DATA_WIDTH=32, C_S_AXI_ADDR_WIDTH=8):
        self.C_S_AXI_DATA_WIDTH = C_S_AXI_DATA_WIDTH
        self.C_S_AXI_ADDR_WIDTH = C_S_AXI_ADDR_WIDTH
        self.reset()

    def reset(self):
        self.ctl = 0
        self.elapsed = 0
        self.count = 0
        self.irq_mask = 0
        self.threshold = 0
        self.aw_full = 0
        self.w_full = 0
        self.address_buffer = 0
        self.data_buffer = 0
        self.strobe_buffer = 0
        self.bvalid = 0
        self.bresp = 0
        self.rvalid = 0
        self.rdata = 0
        self.rresp = 0

    def process(self, axi_awaddr, axi_awvalid, axi_wdata, axi_wstrb, axi_wvalid, axi_bready, axi_araddr, axi_arvalid, axi_rready):
        awready = int(not self.aw_full and not self.bvalid)
        wready = int(not self.w_full and not self.bvalid)
        arready = int(not self.rvalid)
        out = {"axi_awready": awready, "axi_wready": wready, "axi_bresp": self.bresp, "axi_bvalid": self.bvalid, "axi_arready": arready, "axi_rdata": self.rdata, "axi_rresp": self.rresp, "axi_rvalid": self.rvalid, "axi_ap_done": int(self.count == 0), "irq": int((self.ctl & 1) != 0 and (self.irq_mask & 1) != 0 and self.count == self.threshold)}
        commit = self.aw_full and self.w_full and not self.bvalid
        if arready and axi_arvalid:
            value = 0
            self.rresp = 0
            if axi_araddr == 0:
                value = self.ctl
            elif axi_araddr == 12:
                value = int(self.count == 0)
            elif axi_araddr == 16:
                value = self.elapsed
            elif axi_araddr == 32:
                value = self.count
            elif axi_araddr == 36:
                value = self.irq_mask
            elif axi_araddr == 40:
                value = self.threshold
            else:
                self.rresp = 2
            self.rdata = value & ((1 << self.C_S_AXI_DATA_WIDTH) - 1)
            self.rvalid = 1
        elif self.rvalid and axi_rready:
            self.rvalid = 0
            self.rdata = 0
            self.rresp = 0
        if self.ctl & 1:
            if self.count != 0:
                self.count -= 1
            else:
                self.elapsed = (self.elapsed + 1) & 0xffffffff
        if self.bvalid and axi_bready:
            self.bvalid = 0
            self.bresp = 0
        if commit:
            self.aw_full = 0
            self.w_full = 0
            self.bvalid = 1
            self.bresp = 0
            target = self.address_buffer
            old = 0
            if target == 0:
                old = self.ctl
            elif target == 16:
                old = self.elapsed
            elif target == 32:
                old = self.count
            elif target == 36:
                old = self.irq_mask
            elif target == 40:
                old = self.threshold
            else:
                self.bresp = 2
            value = old
            for j in range(4):
                if j < self.C_S_AXI_DATA_WIDTH // 8 and ((self.strobe_buffer >> j) & 1):
                    byte_mask = 255 << (8 * j)
                    value = (value & (0xffffffff ^ byte_mask)) | (self.data_buffer & byte_mask)
            if target == 0:
                self.ctl = value
                if self.strobe_buffer & 15:
                    self.elapsed = 0
            elif target == 16:
                self.elapsed = value
            elif target == 32:
                self.count = value
            elif target == 36:
                self.irq_mask = value
            elif target == 40:
                self.threshold = value
        else:
            if awready and axi_awvalid:
                self.aw_full = 1
                self.address_buffer = axi_awaddr
            if wready and axi_wvalid:
                self.w_full = 1
                self.data_buffer = axi_wdata
                self.strobe_buffer = axi_wstrb
        return out
