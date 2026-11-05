class CustomByteEnableRam:
    def __init__(self, XLEN=32, LINES=8192):
        self.XLEN = XLEN
        self.LINES = LINES
        self.reset()

    def reset(self):
        self.ram = [0] * self.LINES
        self.addr_a_q = 0
        self.addr_b_q = 0
        self.en_a_q = 0
        self.en_b_q = 0
        self.be_a_q = 0
        self.be_b_q = 0
        self.data_a_q = 0
        self.data_b_q = 0
        self.out_a = 0
        self.out_b = 0

    def process(self, addr_a, en_a, be_a, data_in_a, addr_b, en_b, be_b, data_in_b):
        out = {"data_out_a": self.out_a, "data_out_b": self.out_b}
        a = self.addr_a_q
        b = self.addr_b_q
        old_a = self.ram[a] if a < self.LINES else 0
        old_b = self.ram[b] if b < self.LINES else 0
        self.out_a = old_a
        self.out_b = old_b
        word_mask = (1 << self.XLEN) - 1
        new_a = old_a
        new_b = old_b
        for lane in range(self.XLEN // 8):
            byte_mask = 255 << (8 * lane)
            if self.en_b_q and ((self.be_b_q >> lane) & 1):
                new_b = (new_b & (word_mask ^ byte_mask)) | (self.data_b_q & byte_mask)
            if self.en_a_q and ((self.be_a_q >> lane) & 1):
                new_a = (new_a & (word_mask ^ byte_mask)) | (self.data_a_q & byte_mask)
            elif a == b and self.en_b_q and ((self.be_b_q >> lane) & 1):
                new_a = (new_a & (word_mask ^ byte_mask)) | (self.data_b_q & byte_mask)
        if b < self.LINES and b != a:
            self.ram[b] = new_b
        if a < self.LINES:
            self.ram[a] = new_a
        self.addr_a_q = addr_a
        self.addr_b_q = addr_b
        self.en_a_q = en_a
        self.en_b_q = en_b
        self.be_a_q = be_a
        self.be_b_q = be_b
        self.data_a_q = data_in_a
        self.data_b_q = data_in_b
        return out
