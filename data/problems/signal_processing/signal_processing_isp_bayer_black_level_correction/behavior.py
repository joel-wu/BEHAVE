def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BayerBlackLevelCorrection:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_pixel,
        in_row_lsb,
        in_col_lsb,
        in_bayer_pattern,
        in_is_linear,
        in_bit_depth,
        in_r_offset,
        in_gr_offset,
        in_gb_offset,
        in_b_offset,
        in_r_sat,
        in_gr_sat,
        in_gb_sat,
        in_b_sat,
    ):
        if in_bit_depth == 0:
            full_scale = 0
        elif in_bit_depth >= 16:
            full_scale = 65535
        else:
            full_scale = (1 << in_bit_depth) - 1

        pos = ((in_row_lsb & 1) << 1) | (in_col_lsb & 1)
        pattern = in_bayer_pattern & 0x3

        if pattern == 0:
            if pos == 0:
                offset, sat = in_r_offset, in_r_sat
            elif pos == 1:
                offset, sat = in_gr_offset, in_gr_sat
            elif pos == 2:
                offset, sat = in_gb_offset, in_gb_sat
            else:
                offset, sat = in_b_offset, in_b_sat
        elif pattern == 1:
            if pos == 0:
                offset, sat = in_b_offset, in_b_sat
            elif pos == 1:
                offset, sat = in_gb_offset, in_gb_sat
            elif pos == 2:
                offset, sat = in_gr_offset, in_gr_sat
            else:
                offset, sat = in_r_offset, in_r_sat
        elif pattern == 2:
            if pos == 0:
                offset, sat = in_gr_offset, in_gr_sat
            elif pos == 1:
                offset, sat = in_r_offset, in_r_sat
            elif pos == 2:
                offset, sat = in_b_offset, in_b_sat
            else:
                offset, sat = in_gb_offset, in_gb_sat
        else:
            if pos == 0:
                offset, sat = in_gb_offset, in_gb_sat
            elif pos == 1:
                offset, sat = in_b_offset, in_b_sat
            elif pos == 2:
                offset, sat = in_r_offset, in_r_sat
            else:
                offset, sat = in_gr_offset, in_gr_sat

        corrected = in_pixel - offset

        if in_is_linear:
            denom = sat - offset
            linfact = min(65535, (full_scale << 14)//denom) if denom > 0 else 0
            value = corrected * linfact
            if value >= 0:
                rounded = (value + 8192) // 16384
            else:
                rounded = -(((-value) + 8192) // 16384)
        else:
            rounded = corrected

        if rounded < 0:
            out_pixel = 0
        elif rounded > full_scale:
            out_pixel = full_scale
        else:
            out_pixel = rounded

        return {"out_pixel": to_unsigned(out_pixel, 16)}
