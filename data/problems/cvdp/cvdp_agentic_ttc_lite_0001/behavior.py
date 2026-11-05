class TtcCounterLite:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.match_value = 0
        self.reload_value = 0
        self.control = 0
        self.status = 0
        self.prescaler = 0
        self.phase = 0
        self.rdata = 0

    def process(self, axi_addr, axi_wdata, axi_write_en, axi_read_en):
        axi_rdata = self.rdata
        interrupt = int(bool(self.status and (self.control & 4)))
        if axi_read_en:
            self.rdata = 0
            if axi_addr == 0:
                self.rdata = self.count
            elif axi_addr == 1:
                self.rdata = self.match_value
            elif axi_addr == 2:
                self.rdata = self.reload_value
            elif axi_addr == 3:
                self.rdata = self.control
            elif axi_addr == 4:
                self.rdata = self.status
            elif axi_addr == 5:
                self.rdata = self.prescaler
        enabled = self.control & 1
        event = 0
        if enabled:
            if self.count == self.match_value:
                event = 1
            elif self.phase == self.prescaler:
                self.phase = 0
                self.count = (self.count + 1) & 65535
                event = int(self.count == self.match_value)
            else:
                self.phase += 1
            if event:
                self.phase = 0
                if self.control & 2:
                    self.count = self.reload_value
                else:
                    self.control &= 6
        else:
            self.phase = 0
        if axi_write_en:
            if axi_addr == 1:
                self.match_value = axi_wdata & 65535
            elif axi_addr == 2:
                self.reload_value = axi_wdata & 65535
            elif axi_addr == 3:
                self.control = axi_wdata & 7
            elif axi_addr == 4:
                if axi_wdata & 1:
                    self.status = 0
            elif axi_addr == 5:
                self.prescaler = axi_wdata & 15
                self.phase = 0
        if not (self.control & 1):
            self.phase = 0
        if event:
            self.status = 1
        return {"axi_rdata": axi_rdata, "interrupt": interrupt}
