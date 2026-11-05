class SetBitStreamCalculator:
    def __init__(self, p_max_set_bit_count_width=8):
        self.p_max_set_bit_count_width = p_max_set_bit_count_width
        self.reset()

    def reset(self):
        self.count = 0
        self.previous_ready = 0

    def process(self, i_ready, i_bit_in):
        old = self.count
        if i_ready and not self.previous_ready:
            self.count = 0
        elif i_ready and i_bit_in and self.count < (1 << self.p_max_set_bit_count_width) - 1:
            self.count += 1
        self.previous_ready = i_ready
        return {"o_set_bit_count": old}
