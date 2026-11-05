class CsrApbInterface:
    def __init__(self):
        self.reset()

    def reset(self):
        self.data_reg = 0
        self.control_reg = 0
        self.interrupt_reg = 0
        self.isr = 0
        self.state = 0
        self.saved_addr = 0
        self.saved_data = 0
        self.saved_write = 0

    def process(self, pselx, penable, pwrite, paddr, pwdata, overflow_is_set, sign_is_set, parity_is_set, zero_is_set):
        debug_state = self.state
        pready = int(self.state >= 2 and pselx and penable)
        prdata = 0
        pslverr = 0
        if pready:
            mapped = self.saved_addr == 16 or self.saved_addr == 20 or self.saved_addr == 24 or self.saved_addr == 28
            pslverr = int(not mapped or (self.saved_write and self.saved_addr == 28))
            if not self.saved_write:
                if self.saved_addr == 16:
                    prdata = self.data_reg
                elif self.saved_addr == 20:
                    prdata = self.control_reg
                elif self.saved_addr == 24:
                    prdata = self.interrupt_reg
                elif self.saved_addr == 28:
                    prdata = self.isr
        self.isr |= overflow_is_set | (sign_is_set << 1) | (parity_is_set << 2) | (zero_is_set << 3)
        if pready:
            if self.saved_write:
                if self.saved_addr == 16:
                    self.data_reg = self.saved_data
                elif self.saved_addr == 20:
                    self.control_reg = self.saved_data
                elif self.saved_addr == 24:
                    self.interrupt_reg = self.saved_data
                    self.isr &= 15 ^ (self.saved_data & 15)
            self.state = 0
        elif not pselx:
            self.state = 0
        elif not penable:
            self.saved_addr = paddr
            self.saved_data = pwdata
            self.saved_write = pwrite
            self.state = 1
        elif self.state == 1:
            self.state = 3 if self.saved_write else 2
        return {"prdata":prdata,"pready":pready,"pslverr":pslverr,"debug_state":debug_state}
