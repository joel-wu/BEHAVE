def to_unsigned(v, b):
    return v & ((1 << b) - 1)


FP8_MAX = 448
U64_MAX = (1 << 64) - 1


class AiMlQuantFp8Fp8DelayedScaling:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_amax, in_scale, in_history_0, in_history_1, in_history_2, in_history_3):
        # Q16.16 history: accumulate the current epoch, reduce, then rotate.
        current = to_unsigned(in_amax, 32)
        h0 = to_unsigned(in_history_0, 32)
        h1 = to_unsigned(in_history_1, 32)
        h2 = to_unsigned(in_history_2, 32)
        h3 = to_unsigned(in_history_3, 32)
        if h0 > current:
            current = h0
        selected_amax = current
        if h1 > selected_amax:
            selected_amax = h1
        if h2 > selected_amax:
            selected_amax = h2
        if h3 > selected_amax:
            selected_amax = h3

        if selected_amax == 0:
            out_scale = to_unsigned(in_scale, 64)
        else:
            out_scale = (FP8_MAX << 48) // selected_amax

        if out_scale == 0:
            out_scale_inv = 0
        else:
            out_scale_inv = (1 << 64) // out_scale
            if out_scale_inv > U64_MAX:
                out_scale_inv = U64_MAX

        return {
            "out_scale": out_scale,
            "out_scale_inv": out_scale_inv,
            "out_history_0": 0,
            "out_history_1": h2,
            "out_history_2": h3,
            "out_history_3": current,
        }
