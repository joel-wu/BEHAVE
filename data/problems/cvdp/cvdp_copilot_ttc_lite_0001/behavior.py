class TtcCounterLite:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.match_value = 0
        self.reload_value = 0
        self.control = 0
        self.interrupt = 0

    def process(self, axi_addr, axi_wdata, axi_write_en, axi_read_en):
        rdata = 0
        interrupt = self.interrupt
        if axi_read_en:
            if axi_addr == 0:
                rdata = self.count
            elif axi_addr == 1:
                rdata = self.match_value
            elif axi_addr == 2:
                rdata = self.reload_value
            elif axi_addr == 3:
                rdata = self.control
            elif axi_addr == 4:
                rdata = self.interrupt
        if self.control & 1:
            if self.count == self.match_value:
                if self.control & 2:
                    self.count = self.reload_value
                if self.control & 4:
                    self.interrupt = 1
            else:
                self.count = (self.count + 1) & 65535
        if axi_write_en:
            if axi_addr == 1:
                self.match_value = axi_wdata & 65535
            elif axi_addr == 2:
                self.reload_value = axi_wdata & 65535
            elif axi_addr == 3:
                self.control = axi_wdata & 7
            elif axi_addr == 4:
                self.interrupt = 0
        return {"axi_rdata": rdata, "interrupt": interrupt}
