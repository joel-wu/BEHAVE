class EthernetMacTx:
    def __init__(self):
        self.reset()

    def reset(self):
        self.memory = [0] * 1024
        self.cfg_read = 0
        self.length = 0
        self.irq_enable = 0
        self.program = 0
        self.phase = 0
        self.offset = 0
        self.irq = 0
        self.valid = 0
        self.data = 0
        self.strb = 0
        self.last = 0

    def process(self, cfg_wr_in, cfg_addr_in, cfg_data_wr_in, axis_tready_in):
        csr = cfg_addr_in & 0xffff
        readback = self.cfg_read
        if csr == 0x7f4:
            readback = self.length
        elif csr == 0x7f8:
            readback = self.irq_enable
        elif csr == 0x7fc:
            readback = (self.program << 1) | int(self.phase != 0)
        out = {"cfg_data_rd_out": 0 if cfg_wr_in else readback, "interrupt_out": self.irq, "axis_tvalid_out": self.valid, "axis_tdata_out": self.data, "axis_tstrb_out": self.strb, "axis_tlast_out": self.last}
        self.cfg_read = self.memory[(cfg_addr_in >> 2) & 1023]
        self.irq = 0
        if self.phase == 0:
            if cfg_wr_in:
                if csr == 0x7f4:
                    requested = cfg_data_wr_in & 0xffff
                    self.length = 1518 if requested > 1518 else requested
                elif csr == 0x7f8:
                    self.irq_enable = cfg_data_wr_in & 1
                elif csr == 0x7fc:
                    self.program = (cfg_data_wr_in >> 1) & 1
                    if cfg_data_wr_in & 1:
                        self.phase = 3 if self.program else 1
                        self.offset = 0
                elif cfg_addr_in < 4096:
                    self.memory[(cfg_addr_in >> 2) & 1023] = cfg_data_wr_in
        elif self.phase == 1:
            total = 64 if self.length < 64 else self.length
            self.data = 0
            self.strb = 0
            for lane in range(4):
                if self.offset + lane < self.length:
                    self.data |= self.memory[(self.offset >> 2) & 1023] & (255 << (8 * lane))
                if self.offset + lane < total:
                    self.strb |= 1 << lane
            self.last = int(self.offset + 4 >= total)
            self.valid = 1
            self.phase = 2
        elif self.phase == 2:
            if axis_tready_in:
                if self.last:
                    self.phase = 3
                else:
                    self.offset = (self.offset + 4) & 2047
                    self.phase = 1
                self.valid = 0
                self.data = 0
                self.strb = 0
                self.last = 0
        else:
            self.phase = 0
            self.program = 0
            self.irq = self.irq_enable
        return out
