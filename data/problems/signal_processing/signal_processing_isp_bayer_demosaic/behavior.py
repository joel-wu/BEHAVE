"""One-pixel Malvar reconstruction with a complete 5x5 neighborhood."""


class BayerDemosaic:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_pattern, in_bit_depth, in_row_lsb, in_col_lsb,
                in_p00, in_p01, in_p02, in_p03, in_p04,
                in_p10, in_p11, in_p12, in_p13, in_p14,
                in_p20, in_p21, in_p22, in_p23, in_p24,
                in_p30, in_p31, in_p32, in_p33, in_p34,
                in_p40, in_p41, in_p42, in_p43, in_p44,
                ):
        c = in_p22
        near_h = in_p21 + in_p23
        near_v = in_p12 + in_p32
        far_h = in_p20 + in_p24
        far_v = in_p02 + in_p42
        diagonal = in_p11 + in_p13 + in_p31 + in_p33

        green = (8*c + 4*(near_h+near_v) - 2*(far_h+far_v)) // 16
        horizontal = (10*c + 8*near_h - 2*far_h + far_v - 2*diagonal) // 16
        vertical = (10*c + 8*near_v - 2*far_v + far_h - 2*diagonal) // 16
        opposite = (12*c + 4*diagonal - 3*(far_h+far_v)) // 16

        red_row = in_pattern & 1
        red_col = (in_pattern & 1) ^ (in_pattern >> 1)
        if in_row_lsb == red_row and in_col_lsb == red_col:
            r, g, b = c, green, opposite
        elif in_row_lsb != red_row and in_col_lsb != red_col:
            r, g, b = opposite, green, c
        elif in_row_lsb == red_row:
            r, g, b = horizontal, c, vertical
        else:
            r, g, b = vertical, c, horizontal

        maximum = (1 << min(in_bit_depth,16)) - 1
        return dict(out_r=min(maximum,max(0,r)),
                    out_g=min(maximum,max(0,g)),
                    out_b=min(maximum,max(0,b)))
