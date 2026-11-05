def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BitmapRoaringBitsetIntersect16w:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15
    ):
        out_w0 = to_unsigned(in_a0 & in_b0, 64)
        out_w1 = to_unsigned(in_a1 & in_b1, 64)
        out_w2 = to_unsigned(in_a2 & in_b2, 64)
        out_w3 = to_unsigned(in_a3 & in_b3, 64)
        out_w4 = to_unsigned(in_a4 & in_b4, 64)
        out_w5 = to_unsigned(in_a5 & in_b5, 64)
        out_w6 = to_unsigned(in_a6 & in_b6, 64)
        out_w7 = to_unsigned(in_a7 & in_b7, 64)
        out_w8 = to_unsigned(in_a8 & in_b8, 64)
        out_w9 = to_unsigned(in_a9 & in_b9, 64)
        out_w10 = to_unsigned(in_a10 & in_b10, 64)
        out_w11 = to_unsigned(in_a11 & in_b11, 64)
        out_w12 = to_unsigned(in_a12 & in_b12, 64)
        out_w13 = to_unsigned(in_a13 & in_b13, 64)
        out_w14 = to_unsigned(in_a14 & in_b14, 64)
        out_w15 = to_unsigned(in_a15 & in_b15, 64)

        out_cardinality = (
            out_w0.bit_count() + out_w1.bit_count() + out_w2.bit_count() + out_w3.bit_count() +
            out_w4.bit_count() + out_w5.bit_count() + out_w6.bit_count() + out_w7.bit_count() +
            out_w8.bit_count() + out_w9.bit_count() + out_w10.bit_count() + out_w11.bit_count() +
            out_w12.bit_count() + out_w13.bit_count() + out_w14.bit_count() + out_w15.bit_count()
        )

        return {
            "out_w0": out_w0,
            "out_w1": out_w1,
            "out_w2": out_w2,
            "out_w3": out_w3,
            "out_w4": out_w4,
            "out_w5": out_w5,
            "out_w6": out_w6,
            "out_w7": out_w7,
            "out_w8": out_w8,
            "out_w9": out_w9,
            "out_w10": out_w10,
            "out_w11": out_w11,
            "out_w12": out_w12,
            "out_w13": out_w13,
            "out_w14": out_w14,
            "out_w15": out_w15,
            "out_cardinality": to_unsigned(out_cardinality, 11),
            "out_intersects": 1 if out_cardinality != 0 else 0,
        }