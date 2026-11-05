class MicrocodeSequencer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pc_state = 0
        self.aux_state = 0
        self.words = [0]*16
        self.count = 0
        self.phase = 0
        self.push_word = 0
        self.pop_word = 0
        self.pop_result = 0

    def process(self, instr_in, d_in, c_n_in, c_inc_in, r_en, cc, ien, oen):
        enabled = int(cc == 0 and ien == 0)
        d_out = 0
        c_n_out = 0
        c_inc_out = 0
        if enabled:
            if instr_in == 1:
                d_out = self.pc_state
                c_inc_out = int(self.phase == 0 and self.pc_state + c_inc_in >= 16)
            elif instr_in == 2:
                d_out = self.aux_state
            elif instr_in == 3:
                d_out = d_in
            elif instr_in == 4:
                total = self.aux_state + d_in + c_n_in
                d_out = total & 15
                c_n_out = total >> 4
            elif instr_in == 14:
                d_out = self.pop_result
        if oen:
            d_out = 0
        out = {"d_out": d_out, "c_n_out": c_n_out, "c_inc_out": c_inc_out,
               "full": int(self.count == 16), "empty": int(self.count == 0)}
        if r_en == 0:
            self.aux_state = d_in
        if enabled and instr_in == 0:
            self.pc_state = c_inc_in
            self.count = 0
            self.phase = 0
            self.pop_result = 0
        elif self.phase == 1:
            if self.count < 16:
                self.words[self.count] = self.push_word
                self.count += 1
            self.phase = 0
        elif self.phase == 2:
            self.pop_word = self.words[self.count - 1] if self.count > 0 else 0
            self.phase = 3
        elif self.phase == 3:
            self.pop_result = self.pop_word
            if self.count > 0:
                self.count -= 1
            self.phase = 0
        elif enabled:
            if instr_in == 1:
                self.pc_state = (self.pc_state + c_inc_in) & 15
            elif instr_in == 11:
                self.push_word = self.pc_state
                self.phase = 1
            elif instr_in == 14:
                self.phase = 2
        return out
