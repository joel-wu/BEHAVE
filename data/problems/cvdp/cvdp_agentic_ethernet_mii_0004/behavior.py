class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mem = [0] * 192
        self.lengths = [0] * 2
        self.write_seq = 0
        self.read_seq = 0
        self.read_gray1 = 0
        self.read_gray2 = 0
        self.write_gray1 = 0
        self.write_gray2 = 0
        self.length = 0
        self.bad = 0
        self.phase = 0
        self.index = 0
        self.frame_length = 0
        self.byte_index = 0
        self.half = 0
        self.fcs = 0
        self.ifg = 0
        self.txd = 0
        self.tx_en = 0
        self.crc = 4294967295
        self.previous_axis = 0
        self.previous_mii = 0

    def process(self, clk_in_level, rst_in, axis_clk_in_level, axis_rst_in, axis_valid_in, axis_data_in, axis_strb_in, axis_last_in):
        old_write_gray = self.write_seq ^ (self.write_seq >> 1)
        old_read_gray = self.read_seq ^ (self.read_seq >> 1)
        if rst_in or axis_rst_in:
            self.write_seq = 0
            self.read_seq = 0
            self.read_gray1 = 0
            self.read_gray2 = 0
            self.write_gray1 = 0
            self.write_gray2 = 0
            self.length = 0
            self.bad = 0
            self.phase = 0
            self.index = 0
            self.frame_length = 0
            self.byte_index = 0
            self.half = 0
            self.fcs = 0
            self.ifg = 0
            self.txd = 0
            self.tx_en = 0
            self.crc = 4294967295
        else:
            if axis_clk_in_level and not self.previous_axis:
                read_seen = self.read_gray2 ^ (self.read_gray2 >> 1)
                full = self.write_seq == ((read_seen + 2) & 3)
                if axis_valid_in and not full:
                    n = self.length
                    for lane in range(4):
                        if (axis_strb_in >> lane) & 1:
                            if n < 1518:
                                cell = (self.write_seq & 1) * 96 + (n >> 4)
                                shift = (n & 15) * 8
                                byte = (axis_data_in >> (lane * 8)) & 255
                                mask = 340282366920938463463374607431768211455 ^ (255 << shift)
                                self.mem[cell] = (self.mem[cell] & mask) | (byte << shift)
                                n = n + 1
                            else:
                                self.bad = 1
                    self.length = n
                    if axis_last_in:
                        self.lengths[self.write_seq & 1] = 0 if self.bad else n
                        self.write_seq = (self.write_seq + 1) & 3
                        self.length = 0
                        self.bad = 0
                self.read_gray2 = self.read_gray1
                self.read_gray1 = old_read_gray
            if clk_in_level and not self.previous_mii:
                self.txd = 0
                self.tx_en = 0
                if self.phase == 0:
                    write_seen = self.write_gray2 ^ (self.write_gray2 >> 1)
                    if self.read_seq != write_seen:
                        n = self.lengths[self.read_seq & 1]
                        if n == 0:
                            self.read_seq = (self.read_seq + 1) & 3
                        else:
                            self.frame_length = n
                            self.crc = 4294967295
                            self.txd = 5
                            self.tx_en = 1
                            self.index = 1
                            self.phase = 1
                elif self.phase == 1:
                    self.tx_en = 1
                    if self.index < 15:
                        self.txd = 5
                        self.index = self.index + 1
                    else:
                        self.txd = 13
                        self.byte_index = 0
                        self.half = 0
                        self.phase = 2
                elif self.phase == 2:
                    cell = (self.read_seq & 1) * 96 + (self.byte_index >> 4)
                    b = (self.mem[cell] >> ((self.byte_index & 15) * 8)) & 255
                    self.txd = (b >> (4 * self.half)) & 15
                    self.tx_en = 1
                    if self.half == 0:
                        self.half = 1
                    else:
                        self.half = 0
                        c = self.crc ^ b
                        for bit in range(8):
                            c = (c >> 1) ^ (3988292384 if c & 1 else 0)
                        self.crc = c
                        if self.byte_index + 1 == self.frame_length:
                            self.fcs = c ^ 4294967295
                            self.index = 0
                            self.phase = 3
                        else:
                            self.byte_index = self.byte_index + 1
                elif self.phase == 3:
                    self.txd = (self.fcs >> (4 * self.index)) & 15
                    self.tx_en = 1
                    if self.index == 7:
                        self.read_seq = (self.read_seq + 1) & 3
                        self.ifg = 24
                        self.phase = 4
                    else:
                        self.index = self.index + 1
                elif self.phase == 4:
                    self.ifg = self.ifg - 1
                    if self.ifg == 0:
                        self.phase = 0
                self.write_gray2 = self.write_gray1
                self.write_gray1 = old_write_gray
        self.previous_axis = axis_clk_in_level
        self.previous_mii = clk_in_level
        seen = self.read_gray2 ^ (self.read_gray2 >> 1)
        return {"axis_ready_out": 0 if rst_in or axis_rst_in or self.write_seq == ((seen + 2) & 3) else 1,
                "mii_txd_out": self.txd, "mii_tx_en_out": self.tx_en}
