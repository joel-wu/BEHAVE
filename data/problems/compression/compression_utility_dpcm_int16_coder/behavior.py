def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CompressionUtilityDpcmInt16Coder:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history_sample = 0

    def process(self, in_load_history, in_history, in_sample):
        load_history = in_load_history & 1
        seed = to_signed(in_history, 16)
        sample = to_signed(in_sample, 16)
        history_for_this_sample = seed if load_history else self.history_sample
        residual_wide = sample - history_for_this_sample
        self.history_sample = sample
        return {"out_residual": to_unsigned(residual_wide, 16)}