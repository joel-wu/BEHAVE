def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RWKV6TokenShift:
    def __init__(self, hidden_size=1, data_width=32):
        self.hidden_size = hidden_size
        self.data_width = data_width
        self.reset()

    def reset(self):
        self.prev_hidden_states = 0

    def process(self, in_hidden_states):
        out_delta = 0
        mask = (1 << self.data_width) - 1
        for i in range(self.hidden_size):
            shift = i * self.data_width
            prev_lane = (self.prev_hidden_states >> shift) & mask
            curr_lane = (in_hidden_states >> shift) & mask
            delta_lane = (prev_lane - curr_lane) & mask
            out_delta |= delta_lane << shift
        self.prev_hidden_states = to_unsigned(in_hidden_states, self.hidden_size * self.data_width)
        return {"out_delta": to_unsigned(out_delta, self.hidden_size * self.data_width)}