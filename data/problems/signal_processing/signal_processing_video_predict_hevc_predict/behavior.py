def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingVideoPredictHevcPredict:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_bidir, in_src0, in_src1, in_p0, in_p1, in_w0, in_w1, in_offset0, in_offset1, in_log2_weight_denom):
        p0 = to_signed(in_p0, 16)
        p1 = to_signed(in_p1, 16)
        w0 = to_signed(in_w0, 8)
        w1 = to_signed(in_w1, 8)
        offset0 = to_signed(in_offset0, 9)
        offset1 = to_signed(in_offset1, 9)
        log2_weight_denom = to_unsigned(in_log2_weight_denom, 4)
        if_internal_offs = 8192
        shift_num = 6

        if to_unsigned(in_bidir, 1):
            shift = log2_weight_denom + shift_num + 1
            round_val = 0 if shift == 0 else (1 << (shift - 1))
            offset = offset0 + offset1
            value = (w0 * (p0 + if_internal_offs) + w1 * (p1 + if_internal_offs) + round_val + (offset << (shift - 1))) >> shift
        else:
            use_src0 = to_unsigned(in_src0, 1)
            p = p0 if use_src0 else p1
            w = w0 if use_src0 else w1
            offset = offset0 if use_src0 else offset1
            shift = log2_weight_denom + shift_num
            round_val = 0 if shift == 0 else (1 << (shift - 1))
            base = (w * (p + if_internal_offs) + round_val) if shift == 0 else ((w * (p + if_internal_offs) + round_val) >> shift)
            value = base + offset

        if value < 0:
            out_pixel = 0
        elif value > 255:
            out_pixel = 255
        else:
            out_pixel = value

        return {"out_pixel": to_unsigned(out_pixel, 8)}