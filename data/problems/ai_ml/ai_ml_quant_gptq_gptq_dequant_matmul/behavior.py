def to_signed(value, width):
    value = value & ((1 << width) - 1)
    if value & (1 << (width - 1)):
        value = value - (1 << width)
    return value


class AffineW4Dot32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_act_0, in_act_1, in_act_2, in_act_3,
        in_act_4, in_act_5, in_act_6, in_act_7,
        in_act_8, in_act_9, in_act_10, in_act_11,
        in_act_12, in_act_13, in_act_14, in_act_15,
        in_act_16, in_act_17, in_act_18, in_act_19,
        in_act_20, in_act_21, in_act_22, in_act_23,
        in_act_24, in_act_25, in_act_26, in_act_27,
        in_act_28, in_act_29, in_act_30, in_act_31,
        in_qweight_0, in_qweight_1, in_qweight_2, in_qweight_3,
        in_scale_0, in_scale_1, in_scale_2, in_scale_3,
        in_zero_0, in_zero_1, in_zero_2, in_zero_3
    ):
        acts = [
            to_signed(in_act_0, 8), to_signed(in_act_1, 8), to_signed(in_act_2, 8), to_signed(in_act_3, 8), to_signed(in_act_4, 8), to_signed(in_act_5, 8), to_signed(in_act_6, 8), to_signed(in_act_7, 8),
            to_signed(in_act_8, 8), to_signed(in_act_9, 8), to_signed(in_act_10, 8), to_signed(in_act_11, 8), to_signed(in_act_12, 8), to_signed(in_act_13, 8), to_signed(in_act_14, 8), to_signed(in_act_15, 8),
            to_signed(in_act_16, 8), to_signed(in_act_17, 8), to_signed(in_act_18, 8), to_signed(in_act_19, 8), to_signed(in_act_20, 8), to_signed(in_act_21, 8), to_signed(in_act_22, 8), to_signed(in_act_23, 8),
            to_signed(in_act_24, 8), to_signed(in_act_25, 8), to_signed(in_act_26, 8), to_signed(in_act_27, 8), to_signed(in_act_28, 8), to_signed(in_act_29, 8), to_signed(in_act_30, 8), to_signed(in_act_31, 8)
        ]
        packed = [
            in_qweight_0 & 0xffffffff, in_qweight_1 & 0xffffffff, in_qweight_2 & 0xffffffff, in_qweight_3 & 0xffffffff
        ]
        scales = [
            to_signed(in_scale_0, 16), to_signed(in_scale_1, 16), to_signed(in_scale_2, 16), to_signed(in_scale_3, 16)
        ]
        zeros = [
            to_signed(in_zero_0, 8), to_signed(in_zero_1, 8), to_signed(in_zero_2, 8), to_signed(in_zero_3, 8)
        ]
        total = 0
        for group in range(4):
            for lane in range(8):
                code = (packed[group] >> (4 * lane)) & 15
                weight = (code - zeros[group]) * scales[group]
                total += acts[8 * group + lane] * weight
        return {"out_sum": total & 0xffffffff}
