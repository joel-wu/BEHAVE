def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class JpegDcPredictor:
    def __init__(self, max_comps=4):
        self.max_comps = max_comps
        self.reset()

    def reset(self):
        self.last_dc_val = [0] * self.max_comps

    def process(self, in_restart, in_comp_idx, in_dc_diff):
        last_dc_val = self.last_dc_val[:]
        if in_restart:
            last_dc_val = [0] * self.max_comps
        ci = in_comp_idx % self.max_comps if self.max_comps != 0 else 0
        dc_diff = to_signed(in_dc_diff, 32)
        pred = last_dc_val[ci] if self.max_comps != 0 else 0
        dc_value = to_signed(pred + dc_diff, 32)
        if self.max_comps != 0:
            last_dc_val[ci] = dc_value
        self.last_dc_val = last_dc_val
        return {"out_dc_value": to_unsigned(dc_value, 32)}