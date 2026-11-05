class AxiRegister:
    def __init__(self, ADDR_WIDTH=32, DATA_WIDTH=32):
        self.ADDR_WIDTH = ADDR_WIDTH
        self.DATA_WIDTH = DATA_WIDTH
        self.reset()

    def reset(self):
        self.beat = 0
        self.start = 0
        self.writeback = 0
        self.done = 0
        self.aw_held = 0
        self.w_held = 0
        self.saved_addr = 0
        self.saved_data = 0
        self.saved_strobe = 0
        self.bvalid = 0
        self.bresp = 0
        self.rvalid = 0
        self.rresp = 0
        self.rdata = 0

    def process(self, awaddr_i, awvalid_i, wdata_i, wvalid_i, wstrb_i, bready_i, araddr_i, arvalid_i, rready_i, done_i):
        awready_o = int(not self.aw_held and not self.bvalid)
        wready_o = int(not self.w_held and not self.bvalid)
        arready_o = int(not self.rvalid)
        bvalid_o = self.bvalid
        bresp_o = self.bresp
        rvalid_o = self.rvalid
        rdata_o = self.rdata
        rresp_o = self.rresp
        beat_o = self.beat
        start_o = self.start
        writeback_o = self.writeback
        commit = self.aw_held and self.w_held and not self.bvalid
        if self.bvalid and bready_i:
            self.bvalid = 0
        if self.rvalid and rready_i:
            self.rvalid = 0
        if arready_o and arvalid_i:
            address = araddr_i & 4095
            self.rdata = 0
            self.rresp = 0
            if address == 256:
                self.rdata = self.beat
            elif address == 512:
                self.rdata = self.start
            elif address == 768:
                self.rdata = self.done
            elif address == 1024:
                self.rdata = self.writeback
            elif address == 1280:
                self.rdata = 65537
            else:
                self.rresp = 2
            self.rdata &= (1 << self.DATA_WIDTH) - 1
            self.rvalid = 1
        self.done |= done_i
        if commit:
            self.aw_held = 0
            self.w_held = 0
            self.bvalid = 1
            address = self.saved_addr
            self.bresp = 0
            if address != 256 and address != 512 and address != 768 and address != 1024:
                self.bresp = 2
            elif self.saved_strobe == (1 << (self.DATA_WIDTH // 8)) - 1:
                if address == 256:
                    self.beat = self.saved_data & 1048575
                elif address == 512:
                    self.start = self.saved_data & 1
                elif address == 768:
                    if self.saved_data & 1:
                        self.done = 0
                else:
                    self.writeback = self.saved_data & 1
        if awready_o and awvalid_i:
            self.saved_addr = awaddr_i & 4095
            self.aw_held = 1
        if wready_o and wvalid_i:
            self.saved_data = wdata_i
            self.saved_strobe = wstrb_i
            self.w_held = 1
        return {"awready_o":awready_o,"wready_o":wready_o,"bresp_o":bresp_o,"bvalid_o":bvalid_o,
                "arready_o":arready_o,"rdata_o":rdata_o,"rvalid_o":rvalid_o,"rresp_o":rresp_o,
                "beat_o":beat_o,"start_o":start_o,"writeback_o":writeback_o}
