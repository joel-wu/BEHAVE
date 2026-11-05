def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlSampleFilterSampleTopkMask:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_k,
        in_logits_0, in_logits_1, in_logits_2, in_logits_3,
        in_logits_4, in_logits_5, in_logits_6, in_logits_7,
        in_logits_8, in_logits_9, in_logits_10, in_logits_11,
        in_logits_12, in_logits_13, in_logits_14, in_logits_15,
        in_mask_0, in_mask_1, in_mask_2, in_mask_3,
        in_mask_4, in_mask_5, in_mask_6, in_mask_7,
        in_mask_8, in_mask_9, in_mask_10, in_mask_11,
        in_mask_12, in_mask_13, in_mask_14, in_mask_15,
    ):
        logits = [
            to_signed(in_logits_0, 16), to_signed(in_logits_1, 16),
            to_signed(in_logits_2, 16), to_signed(in_logits_3, 16),
            to_signed(in_logits_4, 16), to_signed(in_logits_5, 16),
            to_signed(in_logits_6, 16), to_signed(in_logits_7, 16),
            to_signed(in_logits_8, 16), to_signed(in_logits_9, 16),
            to_signed(in_logits_10, 16), to_signed(in_logits_11, 16),
            to_signed(in_logits_12, 16), to_signed(in_logits_13, 16),
            to_signed(in_logits_14, 16), to_signed(in_logits_15, 16),
        ]
        masks = [
            in_mask_0 & 1, in_mask_1 & 1, in_mask_2 & 1, in_mask_3 & 1,
            in_mask_4 & 1, in_mask_5 & 1, in_mask_6 & 1, in_mask_7 & 1,
            in_mask_8 & 1, in_mask_9 & 1, in_mask_10 & 1, in_mask_11 & 1,
            in_mask_12 & 1, in_mask_13 & 1, in_mask_14 & 1, in_mask_15 & 1,
        ]

        k = in_k & 255
        keep = [0] * 16
        for i in range(16):
            greater = 0
            for j in range(16):
                if masks[j] == 0 and logits[j] > logits[i]:
                    greater += 1
            if masks[i] == 0 and k > 0 and greater < k:
                keep[i] = 1

        return {
            "out_keep_0": to_unsigned(keep[0], 1),
            "out_keep_1": to_unsigned(keep[1], 1),
            "out_keep_2": to_unsigned(keep[2], 1),
            "out_keep_3": to_unsigned(keep[3], 1),
            "out_keep_4": to_unsigned(keep[4], 1),
            "out_keep_5": to_unsigned(keep[5], 1),
            "out_keep_6": to_unsigned(keep[6], 1),
            "out_keep_7": to_unsigned(keep[7], 1),
            "out_keep_8": to_unsigned(keep[8], 1),
            "out_keep_9": to_unsigned(keep[9], 1),
            "out_keep_10": to_unsigned(keep[10], 1),
            "out_keep_11": to_unsigned(keep[11], 1),
            "out_keep_12": to_unsigned(keep[12], 1),
            "out_keep_13": to_unsigned(keep[13], 1),
            "out_keep_14": to_unsigned(keep[14], 1),
            "out_keep_15": to_unsigned(keep[15], 1),
        }
