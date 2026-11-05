class DmaTransferEngine:
    def __init__(self, DMA_B=0, DMA_HW=1, DMA_W=2):
        self.DMA_B = DMA_B
        self.DMA_HW = DMA_HW
        self.DMA_W = DMA_W
        self.reset()

    def reset(self):
        self.control = 0
        self.source = 0
        self.destination = 0
        self.buffer = 0
        self.remaining = 0
        self.phase = 0

    def process(self, addr, we, wd, dma_req, bus_grant, rd_m):
        source_code = (self.control >> 4) & 3
        dest_code = (self.control >> 6) & 3
        source_bytes = 1 if source_code == self.DMA_B else (2 if source_code == self.DMA_HW else (4 if source_code == self.DMA_W else 0))
        dest_bytes = 1 if dest_code == self.DMA_B else (2 if dest_code == self.DMA_HW else (4 if dest_code == self.DMA_W else 0))
        rd = 0
        if not we:
            if addr == 0:
                rd = self.control
            elif addr == 4:
                rd = self.source
            elif addr == 8:
                rd = self.destination
        active = int(self.phase != 0)
        addr_m = 0
        we_m = 0
        wd_m = 0
        size_m = 0
        if self.phase == 1:
            addr_m = self.source
            size_m = source_code
        elif self.phase == 2:
            addr_m = self.destination
            we_m = 1
            size_m = dest_code
            wd_m = (self.buffer & ((1 << (8 * dest_bytes)) - 1)) << (8 * (self.destination & 3))
        out = {"rd": rd, "bus_req": active, "bus_lock": active, "addr_m": addr_m, "we_m": we_m, "wd_m": wd_m, "size_m": size_m}
        if self.phase == 0:
            if we:
                if addr == 0:
                    self.control = wd & 1023
                elif addr == 4:
                    self.source = wd
                elif addr == 8:
                    self.destination = wd
            elif dma_req and (self.control & 15) != 0 and source_bytes != 0 and dest_bytes != 0:
                if (self.source & (source_bytes - 1)) == 0 and (self.destination & (dest_bytes - 1)) == 0:
                    self.remaining = self.control & 15
                    self.phase = 1
        elif bus_grant:
            if self.phase == 1:
                self.buffer = (rd_m >> (8 * (self.source & 3))) & ((1 << (8 * source_bytes)) - 1)
                if self.control & 256:
                    self.source = (self.source + source_bytes) & 0xffffffff
                self.phase = 2
            else:
                if self.control & 512:
                    self.destination = (self.destination + dest_bytes) & 0xffffffff
                self.remaining -= 1
                self.phase = 0 if self.remaining == 0 else 1
        return out
