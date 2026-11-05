class DecodeFirstbit:
    def __init__(self, InWidth_g=32, InReg_g=1, OutReg_g=1, PlRegs_g=1):
        self.InWidth_g = InWidth_g
        self.LATENCY = InReg_g+OutReg_g+PlRegs_g
        self.INDEX_BITS = max(1,(InWidth_g-1).bit_length())
        self.ENTRY_BITS = self.INDEX_BITS+2
        self.HISTORY_MASK = (1 << (self.ENTRY_BITS*self.LATENCY))-1
        self.reset()

    def reset(self):
        self.history = 0

    def process(self, In_Data, In_Valid):
        index = 0
        found = 0
        for bit in range(self.InWidth_g):
            if not found and ((In_Data>>bit)&1):
                index = bit
                found = 1
        entry = index|(found<<self.INDEX_BITS)|((In_Valid&1)<<(self.INDEX_BITS+1))
        if self.LATENCY:
            old = self.history >> (self.ENTRY_BITS*(self.LATENCY-1))
            self.history = ((self.history<<self.ENTRY_BITS)|entry)&self.HISTORY_MASK
        else:
            old = entry
        return {"Out_FirstBit":old&((1<<self.INDEX_BITS)-1),
                "Out_Found":(old>>self.INDEX_BITS)&1,
                "Out_Valid":(old>>(self.INDEX_BITS+1))&1}
