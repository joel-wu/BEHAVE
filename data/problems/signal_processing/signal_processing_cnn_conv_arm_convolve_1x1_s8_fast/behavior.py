def signed(v, width=32):
    v &= (1 << width) - 1
    return v - (1 << width) if v >= (1 << (width - 1)) else v


def requantize(value, multiplier, shift):
    scaled = value << max(shift, 0)
    high = (scaled * multiplier + (1 << 30)) >> 31
    right = max(-shift, 0)
    if right == 0:
        return high
    magnitude = (abs(high) + (1 << (right - 1))) >> right
    return -magnitude if high < 0 else magnitude


class SignalProcessingCnnConvArmConvolve1x1S8Fast:
    def __init__(self, in_ch=8, out_ch=8):
        self.in_ch = in_ch
        self.out_ch = out_ch
        self.reset()

    def reset(self):
        pass

    def process(self, in_input_ch, in_output_ch, in_input_offset, in_output_offset,
                in_activation_min, in_activation_max, in_input_data, in_bias_valid,
                in_bias_data, in_filter_data, in_multiplier, in_shift):
        offset = signed(in_input_offset, 9)
        output_offset = signed(in_output_offset, 8)
        lo, hi = signed(in_activation_min, 8), signed(in_activation_max, 8)
        if (in_input_ch != self.in_ch or in_output_ch != self.out_ch
                or offset < -127 or offset > 128 or lo > hi):
            return {"out_output_ch": 0, "out_output_data": 0, "out_error": 1}
        packed = 0
        for oc in range(self.out_ch):
            value = signed(in_bias_data >> (32 * oc)) if in_bias_valid else 0
            for ic in range(self.in_ch):
                x = signed(in_input_data >> (8 * ic), 8)
                w = signed(in_filter_data >> (8 * (oc * self.in_ch + ic)), 8)
                value += (x + offset) * w
            multiplier = signed(in_multiplier >> (32 * oc))
            shift = signed(in_shift >> (32 * oc))
            if multiplier < 0 or shift < -31 or shift > 30:
                return {"out_output_ch": 0, "out_output_data": 0, "out_error": 1}
            scaled = value << max(shift, 0)
            if scaled < -(1 << 31) or scaled >= (1 << 31):
                return {"out_output_ch": 0, "out_output_data": 0, "out_error": 1}
            output = requantize(value, multiplier, shift) + output_offset
            packed |= (min(hi, max(lo, output)) & 255) << (8 * oc)
        return {"out_output_ch": self.out_ch, "out_output_data": packed, "out_error": 0}
