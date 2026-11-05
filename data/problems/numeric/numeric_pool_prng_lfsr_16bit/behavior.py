def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class LfsrWaySelector:
    def __init__(self, seed=0, width=16):
        self.seed = to_unsigned(seed, 16)
        self.width = width
        self.logwidth = (width - 1).bit_length()
        self.reset()

    def reset(self):
        self.shift_q = self.seed

    def process(self, in_en):
        shift_q = self.shift_q
        shift_in = 1 ^ (((shift_q >> 15) ^ (shift_q >> 12) ^ (shift_q >> 5) ^ (shift_q >> 1)) & 1)
        shift_q_next = to_unsigned(((shift_q << 1) | shift_in), 16) if in_en else shift_q
        self.shift_q = shift_q_next
        out_refill_way_bin = to_unsigned(shift_q_next, self.logwidth)
        out_refill_way_oh = to_unsigned(1 << out_refill_way_bin, self.width)
        return {
            "out_refill_way_oh": out_refill_way_oh,
            "out_refill_way_bin": out_refill_way_bin,
        }