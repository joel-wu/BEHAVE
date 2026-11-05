class UniversalShiftRegister:
    def __init__(self, N=8):
        self.N = N
        self.reset()

    def reset(self):
        self.q_state = 0

    def process(self, mode_sel, shift_dir, serial_in, parallel_in):
        old = self.q_state
        serial = (old >> (self.N - 1)) & 1 if shift_dir else old & 1
        if mode_sel == 1:
            if shift_dir:
                self.q_state = ((old << 1) | serial_in) & ((1 << self.N) - 1)
            else:
                self.q_state = (old >> 1) | (serial_in << (self.N - 1))
        elif mode_sel == 2:
            if shift_dir:
                self.q_state = ((old << 1) | serial) & ((1 << self.N) - 1)
            else:
                self.q_state = (old >> 1) | (serial << (self.N - 1))
        elif mode_sel == 3:
            self.q_state = parallel_in & ((1 << self.N) - 1)
        return {"q": old, "serial_out": serial}
