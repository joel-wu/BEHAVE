def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DataSystemsBitmapRoaringBitsetUnion16w:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = 0

    def process(
        self,
        in_a_w0, in_a_w1, in_a_w2, in_a_w3,
        in_a_w4, in_a_w5, in_a_w6, in_a_w7,
        in_a_w8, in_a_w9, in_a_w10, in_a_w11,
        in_a_w12, in_a_w13, in_a_w14, in_a_w15,
        in_b_w0, in_b_w1, in_b_w2, in_b_w3,
        in_b_w4, in_b_w5, in_b_w6, in_b_w7,
        in_b_w8, in_b_w9, in_b_w10, in_b_w11,
        in_b_w12, in_b_w13, in_b_w14, in_b_w15
    ):
        a = [
            in_a_w0, in_a_w1, in_a_w2, in_a_w3,
            in_a_w4, in_a_w5, in_a_w6, in_a_w7,
            in_a_w8, in_a_w9, in_a_w10, in_a_w11,
            in_a_w12, in_a_w13, in_a_w14, in_a_w15,
        ]
        b = [
            in_b_w0, in_b_w1, in_b_w2, in_b_w3,
            in_b_w4, in_b_w5, in_b_w6, in_b_w7,
            in_b_w8, in_b_w9, in_b_w10, in_b_w11,
            in_b_w12, in_b_w13, in_b_w14, in_b_w15,
        ]
        out = [to_unsigned(a[i], 64) | to_unsigned(b[i], 64) for i in range(16)]
        card = 0
        for i in range(16):
            card += out[i].bit_count()
        return {
            "out_w0": to_unsigned(out[0], 64),
            "out_w1": to_unsigned(out[1], 64),
            "out_w2": to_unsigned(out[2], 64),
            "out_w3": to_unsigned(out[3], 64),
            "out_w4": to_unsigned(out[4], 64),
            "out_w5": to_unsigned(out[5], 64),
            "out_w6": to_unsigned(out[6], 64),
            "out_w7": to_unsigned(out[7], 64),
            "out_w8": to_unsigned(out[8], 64),
            "out_w9": to_unsigned(out[9], 64),
            "out_w10": to_unsigned(out[10], 64),
            "out_w11": to_unsigned(out[11], 64),
            "out_w12": to_unsigned(out[12], 64),
            "out_w13": to_unsigned(out[13], 64),
            "out_w14": to_unsigned(out[14], 64),
            "out_w15": to_unsigned(out[15], 64),
            "out_cardinality": to_unsigned(card, 11),
        }