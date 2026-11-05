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


class SignalProcessingCnnElementwiseArmElementwiseAddS8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_input_1, in_input_2, in_input_1_offset,
                in_input_1_mult, in_input_1_shift, in_input_2_offset,
                in_input_2_mult, in_input_2_shift, in_left_shift, in_out_offset,
                in_out_mult, in_out_shift, in_out_activation_min,
                in_out_activation_max):
        a, b = signed(in_input_1, 8), signed(in_input_2, 8)
        offset1, offset2 = signed(in_input_1_offset), signed(in_input_2_offset)
        shift1, shift2, shift_out = signed(in_input_1_shift), signed(in_input_2_shift), signed(in_out_shift)
        m1, m2, mo = signed(in_input_1_mult), signed(in_input_2_mult), signed(in_out_mult)
        left, offset = signed(in_left_shift), signed(in_out_offset)
        lo, hi = signed(in_out_activation_min), signed(in_out_activation_max)
        if (offset1 < -127 or offset1 > 128 or offset2 < -127 or offset2 > 128
                or m1 < 0 or m2 < 0 or mo < 0 or left < 0 or left > 20
                or shift1 < -31 or shift1 > 0 or shift2 < -31 or shift2 > 0
                or shift_out < -31 or shift_out > 0 or offset < -128 or offset > 127
                or lo < -128 or hi > 127 or lo > hi):
            return {"out_output": 0, "out_error": 1}
        r1 = requantize((a + offset1) << left, m1, shift1)
        r2 = requantize((b + offset2) << left, m2, shift2)
        value = requantize(r1 + r2, mo, shift_out) + offset
        return {"out_output": min(hi, max(lo, value)) & 255, "out_error": 0}
