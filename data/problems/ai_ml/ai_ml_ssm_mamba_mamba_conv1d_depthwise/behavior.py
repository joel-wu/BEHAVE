def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class MambaConv1DDepthwise:
    D_CONV = 4
    DATA_W = 32

    def __init__(self, d_conv=4, data_w=32):
        assert 1 <= d_conv <= 4 and 8 <= data_w <= 32
        self.D_CONV = d_conv
        self.DATA_W = data_w
        self.reset()

    def reset(self):
        # Boxcar default: weight = [1, 1, 1, 1]. With this default the
        # block computes a simple 4-tap moving sum over the causal window,
        # giving non-zero output for any non-zero input even before any
        # cfg write occurs.
        self.weight = [1] * self.D_CONV
        self.conv_state = [0] * self.D_CONV

    def process(self, in_x, cfg_valid, cfg_we, cfg_addr, cfg_wdata):
        # Atomic config write: writes at cfg_addr in [0, D_CONV) take effect
        # for the same accepted transaction they are asserted on.
        cfg_valid = cfg_valid & 1
        cfg_we = cfg_we & 1
        cfg_addr = cfg_addr & 0x3
        if cfg_valid and cfg_we and cfg_addr < self.D_CONV:
            self.weight[cfg_addr] = to_signed(cfg_wdata, self.DATA_W)

        x_new = to_signed(in_x, self.DATA_W)
        next_state = self.conv_state[1:] + [x_new]
        preact = 0
        for k in range(self.D_CONV):
            preact += next_state[k] * self.weight[k]
        self.conv_state = next_state
        return {"out_y": to_unsigned(preact, self.DATA_W)}
