def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BayerOECF:
    BPP = 12
    LUT_DEPTH = 1 << BPP

    def __init__(self, BPP=12, LUT_DATA_W=16):
        assert 1 <= BPP <= LUT_DATA_W <= 16, "require 1 <= BPP <= LUT_DATA_W <= 16"
        self.BPP = BPP
        self.LUT_DEPTH = 1 << BPP
        self.reset()

    def reset(self):
        self.r_lut = [0] * self.LUT_DEPTH
        self.gr_lut = [0] * self.LUT_DEPTH
        self.gb_lut = [0] * self.LUT_DEPTH
        self.b_lut = [0] * self.LUT_DEPTH

    def process(self, in_sample, in_x_lsb, in_y_lsb, in_bayer_pattern):
        x = in_x_lsb & 1
        y = in_y_lsb & 1
        addr = in_sample & (self.LUT_DEPTH - 1)

        if in_bayer_pattern == 0:
            if y == 0:
                lut_value = self.r_lut[addr] if x == 0 else self.gr_lut[addr]
            else:
                lut_value = self.gb_lut[addr] if x == 0 else self.b_lut[addr]
        elif in_bayer_pattern == 1:
            if y == 0:
                lut_value = self.b_lut[addr] if x == 0 else self.gb_lut[addr]
            else:
                lut_value = self.gr_lut[addr] if x == 0 else self.r_lut[addr]
        elif in_bayer_pattern == 2:
            if y == 0:
                lut_value = self.gr_lut[addr] if x == 0 else self.r_lut[addr]
            else:
                lut_value = self.b_lut[addr] if x == 0 else self.gb_lut[addr]
        else:
            if y == 0:
                lut_value = self.gb_lut[addr] if x == 0 else self.b_lut[addr]
            else:
                lut_value = self.r_lut[addr] if x == 0 else self.gr_lut[addr]

        max_value = (1 << self.BPP) - 1
        out_sample = max_value if lut_value > max_value else lut_value
        return {"out_sample": to_unsigned(out_sample, self.BPP)}
