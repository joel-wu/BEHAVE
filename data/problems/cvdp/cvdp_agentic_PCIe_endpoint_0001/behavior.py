class Behavior:
    def __init__(self, ADDR_WIDTH=64, DATA_WIDTH=128):
        self.ADDR_WIDTH = ADDR_WIDTH
        self.DATA_WIDTH = DATA_WIDTH
        self.reset()

    def reset(self):
        self.phase = 0
        self.tx_valid = 0
        self.tx_data = 0
        self.cmd_write = 0
        self.cmd_addr = 0
        self.cmd_data = 0
        self.host_addr = 0
        self.host_payload = 0
        self.source = 0
        self.destination = 0
        self.left = 0
        self.previous_dma = 0
        self.dma_complete = 0
        self.dma_error = 0
        self.msix_interrupt = 0
        self.previous_clk = 0
        self.P = self.DATA_WIDTH - self.ADDR_WIDTH - 8
        self.AMASK = (1 << self.ADDR_WIDTH) - 1
        self.PMASK = (1 << self.P) - 1

    def process(self, clk_level, reset_n, pcie_rx_tlp, pcie_rx_valid, pcie_tx_ready, dma_request, dma_src, dma_dst, dma_words, mem_ready, mem_rsp_valid, mem_error, mem_rdata):
        if not reset_n:
            self.phase = 0
            self.tx_valid = 0
            self.tx_data = 0
            self.cmd_write = 0
            self.cmd_addr = 0
            self.cmd_data = 0
            self.host_addr = 0
            self.host_payload = 0
            self.source = 0
            self.destination = 0
            self.left = 0
            self.previous_dma = 0
            self.dma_complete = 0
            self.dma_error = 0
            self.msix_interrupt = 0
        elif clk_level and not self.previous_clk:
            self.dma_complete = 0
            self.dma_error = 0
            self.msix_interrupt = 0
            if self.phase == 0:
                if self.tx_valid:
                    if pcie_tx_ready:
                        self.tx_valid = 0
                elif dma_request:
                    if not self.previous_dma:
                        self.source = dma_src
                        self.destination = dma_dst
                        self.left = dma_words
                        if dma_words == 0:
                            self.dma_complete = 1
                            self.msix_interrupt = 1
                        else:
                            self.cmd_addr = dma_src
                            self.cmd_data = 0
                            self.cmd_write = 0
                            self.phase = 3
                elif pcie_rx_valid:
                    code = pcie_rx_tlp >> (self.DATA_WIDTH - 8)
                    self.host_addr = (pcie_rx_tlp >> self.P) & self.AMASK
                    self.host_payload = pcie_rx_tlp & self.PMASK
                    if code == 1 or code == 2:
                        self.cmd_addr = self.host_addr
                        self.cmd_data = self.host_payload if code == 1 else 0
                        self.cmd_write = 1 if code == 1 else 0
                        self.phase = 1
                    else:
                        self.tx_data = (255 << (self.DATA_WIDTH - 8)) | (self.host_addr << self.P)
                        self.tx_valid = 1
            elif self.phase == 1:
                if mem_ready:
                    self.phase = 2
            elif self.phase == 2:
                if mem_rsp_valid:
                    code = 254 if mem_error else (129 if self.cmd_write else 130)
                    data = 0 if mem_error else (self.host_payload if self.cmd_write else (mem_rdata & self.PMASK))
                    self.tx_data = (code << (self.DATA_WIDTH - 8)) | (self.host_addr << self.P) | data
                    self.tx_valid = 1
                    self.phase = 0
            elif self.phase == 3:
                if mem_ready:
                    self.phase = 4
            elif self.phase == 4:
                if mem_rsp_valid:
                    if mem_error:
                        self.phase = 0
                        self.dma_complete = 1
                        self.dma_error = 1
                    else:
                        self.cmd_addr = self.destination
                        self.cmd_data = mem_rdata
                        self.cmd_write = 1
                        self.phase = 5
            elif self.phase == 5:
                if mem_ready:
                    self.phase = 6
            elif self.phase == 6:
                if mem_rsp_valid:
                    if mem_error or self.left == 1:
                        self.phase = 0
                        self.dma_complete = 1
                        self.dma_error = 1 if mem_error else 0
                        self.msix_interrupt = 0 if mem_error else 1
                        self.left = 0
                    else:
                        self.left = self.left - 1
                        self.source = (self.source + 1) & self.AMASK
                        self.destination = (self.destination + 1) & self.AMASK
                        self.cmd_addr = self.source
                        self.cmd_data = 0
                        self.cmd_write = 0
                        self.phase = 3
            self.previous_dma = dma_request
        self.previous_clk = clk_level
        idle = reset_n and self.phase == 0 and not self.tx_valid
        return {"pcie_rx_ready": 1 if idle and not dma_request else 0,
                "pcie_tx_tlp": self.tx_data, "pcie_tx_valid": self.tx_valid,
                "dma_ready": 1 if idle else 0, "dma_complete": self.dma_complete,
                "dma_error": self.dma_error, "msix_interrupt": self.msix_interrupt,
                "mem_valid": 1 if reset_n and (self.phase == 1 or self.phase == 3 or self.phase == 5) else 0,
                "mem_write": self.cmd_write, "mem_addr": self.cmd_addr, "mem_wdata": self.cmd_data,
                "mem_rsp_ready": 1 if reset_n and (self.phase == 2 or self.phase == 4 or self.phase == 6) else 0}
