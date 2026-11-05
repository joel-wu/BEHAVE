def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BayerWhiteBalance:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_pixel, in_row, in_col, in_bayer_pattern, in_bit_depth,
                in_r_gain_u16p8, in_b_gain_u16p8):
        row_parity = in_row & 1
        col_parity = in_col & 1
        max_value = (1 << min(in_bit_depth,16)) - 1

        if in_bayer_pattern == 0:
            if row_parity == 0 and col_parity == 0:
                scaled = (in_pixel * in_r_gain_u16p8) >> 8
            elif row_parity == 1 and col_parity == 1:
                scaled = (in_pixel * in_b_gain_u16p8) >> 8
            else:
                scaled = in_pixel
        elif in_bayer_pattern == 1:
            if row_parity == 0 and col_parity == 0:
                scaled = (in_pixel * in_b_gain_u16p8) >> 8
            elif row_parity == 1 and col_parity == 1:
                scaled = (in_pixel * in_r_gain_u16p8) >> 8
            else:
                scaled = in_pixel
        elif in_bayer_pattern == 2:
            if row_parity == 0 and col_parity == 1:
                scaled = (in_pixel * in_r_gain_u16p8) >> 8
            elif row_parity == 1 and col_parity == 0:
                scaled = (in_pixel * in_b_gain_u16p8) >> 8
            else:
                scaled = in_pixel
        else:
            if row_parity == 0 and col_parity == 1:
                scaled = (in_pixel * in_b_gain_u16p8) >> 8
            elif row_parity == 1 and col_parity == 0:
                scaled = (in_pixel * in_r_gain_u16p8) >> 8
            else:
                scaled = in_pixel

        clipped = max_value if scaled > max_value else scaled
        return {"out_pixel": to_unsigned(clipped, 16)}
