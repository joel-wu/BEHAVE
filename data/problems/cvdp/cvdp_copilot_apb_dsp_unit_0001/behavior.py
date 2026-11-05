class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_clock = 0
        self.last_sram = 0
        self.operand1 = 0
        self.operand2 = 0
        self.mode = 0
        self.write_address = 0
        self.write_data = 0
        self.result = 0
        self.prdata = 0
        self.error = 0
        self.memory = [0] * 1024

    def process(self, pclk_level, presetn, pselx, penable, pwrite, paddr, pwdata, sram_valid):
        old_mode = self.mode
        old_address = self.write_address
        old_data = self.write_data
        old_result = self.result
        if not presetn:
            self.operand1 = 0
            self.operand2 = 0
            self.mode = 0
            self.write_address = 0
            self.write_data = 0
            self.result = 0
            self.prdata = 0
            self.error = 0
        else:
            if pclk_level and not self.last_clock:
                if old_mode == 1:
                    self.result = (self.memory[self.operand1] + self.memory[self.operand2]) & 255
                elif old_mode == 2:
                    self.result = (self.memory[self.operand1] * self.memory[self.operand2]) & 255
                if pselx and penable:
                    self.error = 0
                    if pwrite:
                        self.prdata = 0
                        if paddr == 0:
                            self.operand1 = pwdata
                        elif paddr == 1:
                            self.operand2 = pwdata
                        elif paddr == 2 and pwdata < 4:
                            self.mode = pwdata
                        elif paddr == 3:
                            self.write_address = pwdata
                        elif paddr == 4:
                            self.write_data = pwdata
                        else:
                            self.error = 1
                    elif paddr == 0:
                        self.prdata = self.operand1
                    elif paddr == 1:
                        self.prdata = self.operand2
                    elif paddr == 2:
                        self.prdata = old_mode
                    elif paddr == 3:
                        self.prdata = old_address
                    elif paddr == 4:
                        self.prdata = old_data
                    elif paddr == 5:
                        self.prdata = old_result
                    else:
                        self.prdata = self.memory[paddr]
            if sram_valid and not self.last_sram and old_mode == 3 and old_address >= 6:
                self.memory[old_address] = old_data
        self.last_clock = pclk_level
        self.last_sram = sram_valid
        return {"pready": presetn, "prdata": self.prdata, "pslverr": self.error}
