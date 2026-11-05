def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class GroupedConvResNeXtGroupedConv:
    NUM_GROUPS    = 4
    OC_PER_GROUP  = 4
    TAPS          = 9
    IC_PER_GROUP  = 4
    WEIGHT_W      = 8
    ACT_W         = 8
    OUT_W         = 22
    NUM_WEIGHTS   = NUM_GROUPS * OC_PER_GROUP * TAPS * IC_PER_GROUP

    def __init__(self):
        self.reset()

    def reset(self):
        # Center-tap identity-ish default: weight = 1 only at tap == 4 and
        # ci == 0; everything else is zero. With this default the block
        # computes total = acts[16] (the center-tap, channel-0 activation),
        # giving non-zero output for non-zero activations even before any
        # cfg write occurs.
        self.weights = [
            [
                [
                    [
                        1 if (tap == 4 and ci == 0) else 0
                        for ci in range(self.IC_PER_GROUP)
                    ]
                    for tap in range(self.TAPS)
                ]
                for _ in range(self.OC_PER_GROUP)
            ]
            for _ in range(self.NUM_GROUPS)
        ]

    def process(
        self,
        in_group,
        in_out_chan_in_group,
        in_act_0, in_act_1, in_act_2, in_act_3,
        in_act_4, in_act_5, in_act_6, in_act_7,
        in_act_8, in_act_9, in_act_10, in_act_11,
        in_act_12, in_act_13, in_act_14, in_act_15,
        in_act_16, in_act_17, in_act_18, in_act_19,
        in_act_20, in_act_21, in_act_22, in_act_23,
        in_act_24, in_act_25, in_act_26, in_act_27,
        in_act_28, in_act_29, in_act_30, in_act_31,
        in_act_32, in_act_33, in_act_34, in_act_35,
        cfg_valid, cfg_we, cfg_addr, cfg_wdata,
    ):
        # Atomic config write: addr maps to (g, co, tap, ci) and the new
        # value takes effect for the same accepted transaction.
        cfg_valid = cfg_valid & 1
        cfg_we = cfg_we & 1
        cfg_addr = cfg_addr & 0x3FF
        if cfg_valid and cfg_we and cfg_addr < self.NUM_WEIGHTS:
            w = to_signed(cfg_wdata, self.WEIGHT_W)
            tmp = cfg_addr
            ci_w = tmp & 0x3
            tmp >>= 2
            tap_w = tmp % self.TAPS
            tmp //= self.TAPS
            co_w = tmp & 0x3
            tmp >>= 2
            g_w = tmp & 0x3
            self.weights[g_w][co_w][tap_w][ci_w] = to_unsigned(w, self.WEIGHT_W)

        g  = in_group & 0x3
        co = in_out_chan_in_group & 0x3
        acts = [
            to_signed(v, self.ACT_W) for v in (
                in_act_0, in_act_1, in_act_2, in_act_3,
                in_act_4, in_act_5, in_act_6, in_act_7,
                in_act_8, in_act_9, in_act_10, in_act_11,
                in_act_12, in_act_13, in_act_14, in_act_15,
                in_act_16, in_act_17, in_act_18, in_act_19,
                in_act_20, in_act_21, in_act_22, in_act_23,
                in_act_24, in_act_25, in_act_26, in_act_27,
                in_act_28, in_act_29, in_act_30, in_act_31,
                in_act_32, in_act_33, in_act_34, in_act_35,
            )
        ]

        total = 0
        for tap in range(self.TAPS):
            for ci in range(self.IC_PER_GROUP):
                w = to_signed(self.weights[g][co][tap][ci], self.WEIGHT_W)
                total += acts[tap * self.IC_PER_GROUP + ci] * w
        return {"out_sum": to_unsigned(total, self.OUT_W)}
