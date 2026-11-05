def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingIspAuxFcs:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_img_r,
        in_img_g,
        in_img_b,
        in_edgemap,
        in_fcs_edge_lo,
        in_fcs_edge_hi,
        in_gain,
        in_intercept,
        in_slope,
    ):
        img_r = to_unsigned(in_img_r, 8)
        img_g = to_unsigned(in_img_g, 8)
        img_b = to_unsigned(in_img_b, 8)
        edgemap = to_signed(in_edgemap, 16)
        fcs_edge_lo = to_unsigned(in_fcs_edge_lo, 8)
        fcs_edge_hi = to_unsigned(in_fcs_edge_hi, 8)
        gain = to_signed(in_gain, 16)
        intercept = to_signed(in_intercept, 16)
        slope = to_signed(in_slope, 16)

        edge_abs = -edgemap if edgemap < 0 else edgemap
        if edge_abs <= fcs_edge_lo:
            uvgain = gain
        elif edge_abs < fcs_edge_hi:
            uvgain = intercept - slope * edgemap
        else:
            uvgain = 0

        def channel_out(c):
            prod = uvgain * c
            biased = (prod // 256) + 128
            if biased < 0:
                biased = 0
            elif biased > 255:
                biased = 255
            return to_unsigned(biased, 8)

        return {
            "out_img_r": channel_out(img_r),
            "out_img_g": channel_out(img_g),
            "out_img_b": channel_out(img_b),
        }
