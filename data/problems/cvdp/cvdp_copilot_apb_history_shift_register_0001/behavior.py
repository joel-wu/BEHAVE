class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_pclk = 0
        self.last_history = 0
        self.gate_enable = 1
        self.control_register = 0
        self.train_history = 0
        self.predict_history = 0
        self.prdata = 0
        self.error_latch = 0

    def process(self, pclk_level, presetn, pselx, penable, pwrite, paddr, pwdata, history_shift_valid, clk_gate_en):
        old_control = self.control_register
        old_train = self.train_history
        old_history = self.predict_history
        if not presetn:
            self.gate_enable = 1
            self.control_register = 0
            self.train_history = 0
            self.predict_history = 0
            self.prdata = 0
            self.error_latch = 0
        else:
            if not pclk_level:
                self.gate_enable = int(not clk_gate_en)
            if pclk_level and not self.last_pclk and self.gate_enable and pselx and penable:
                valid = int(paddr == 0 or paddr == 1 or (paddr == 2 and not pwrite))
                self.error_latch = 1 - valid
                self.prdata = 0
                if valid:
                    if pwrite:
                        if paddr == 0:
                            self.control_register = pwdata & 15
                        else:
                            self.train_history = pwdata & 127
                    elif paddr == 0:
                        self.prdata = old_control
                    elif paddr == 1:
                        self.prdata = old_train
                    else:
                        self.prdata = old_history
            if history_shift_valid and not self.last_history:
                if old_control & 4:
                    self.predict_history = (old_train << 1) | ((old_control >> 3) & 1)
                elif old_control & 1:
                    self.predict_history = ((old_history << 1) | ((old_control >> 1) & 1)) & 255
        self.last_pclk = pclk_level
        self.last_history = history_shift_valid
        full = int(self.predict_history == 255)
        return {"pready": int(presetn and self.gate_enable), "prdata": self.prdata,
                "pslverr": self.error_latch, "error_flag": self.error_latch,
                "interrupt_error": self.error_latch, "history_full": full,
                "history_empty": int(self.predict_history == 0), "interrupt_full": full}
