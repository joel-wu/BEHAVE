def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat_q15_from_q30_sum(v):
    t = v >> 15
    if t > 32767:
        t = 32767
    elif t < -32768:
        t = -32768
    return to_unsigned(t, 16)


class Q15Convolution8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7
    ):
        a0 = to_signed(in_a0, 16)
        a1 = to_signed(in_a1, 16)
        a2 = to_signed(in_a2, 16)
        a3 = to_signed(in_a3, 16)
        a4 = to_signed(in_a4, 16)
        a5 = to_signed(in_a5, 16)
        a6 = to_signed(in_a6, 16)
        a7 = to_signed(in_a7, 16)

        b0 = to_signed(in_b0, 16)
        b1 = to_signed(in_b1, 16)
        b2 = to_signed(in_b2, 16)
        b3 = to_signed(in_b3, 16)
        b4 = to_signed(in_b4, 16)
        b5 = to_signed(in_b5, 16)
        b6 = to_signed(in_b6, 16)
        b7 = to_signed(in_b7, 16)

        s0 = a0 * b0
        s1 = a0 * b1 + a1 * b0
        s2 = a0 * b2 + a1 * b1 + a2 * b0
        s3 = a0 * b3 + a1 * b2 + a2 * b1 + a3 * b0
        s4 = a0 * b4 + a1 * b3 + a2 * b2 + a3 * b1 + a4 * b0
        s5 = a0 * b5 + a1 * b4 + a2 * b3 + a3 * b2 + a4 * b1 + a5 * b0
        s6 = a0 * b6 + a1 * b5 + a2 * b4 + a3 * b3 + a4 * b2 + a5 * b1 + a6 * b0
        s7 = a0 * b7 + a1 * b6 + a2 * b5 + a3 * b4 + a4 * b3 + a5 * b2 + a6 * b1 + a7 * b0
        s8 = a1 * b7 + a2 * b6 + a3 * b5 + a4 * b4 + a5 * b3 + a6 * b2 + a7 * b1
        s9 = a2 * b7 + a3 * b6 + a4 * b5 + a5 * b4 + a6 * b3 + a7 * b2
        s10 = a3 * b7 + a4 * b6 + a5 * b5 + a6 * b4 + a7 * b3
        s11 = a4 * b7 + a5 * b6 + a6 * b5 + a7 * b4
        s12 = a5 * b7 + a6 * b6 + a7 * b5
        s13 = a6 * b7 + a7 * b6
        s14 = a7 * b7

        return {
            "out_y0": sat_q15_from_q30_sum(s0),
            "out_y1": sat_q15_from_q30_sum(s1),
            "out_y2": sat_q15_from_q30_sum(s2),
            "out_y3": sat_q15_from_q30_sum(s3),
            "out_y4": sat_q15_from_q30_sum(s4),
            "out_y5": sat_q15_from_q30_sum(s5),
            "out_y6": sat_q15_from_q30_sum(s6),
            "out_y7": sat_q15_from_q30_sum(s7),
            "out_y8": sat_q15_from_q30_sum(s8),
            "out_y9": sat_q15_from_q30_sum(s9),
            "out_y10": sat_q15_from_q30_sum(s10),
            "out_y11": sat_q15_from_q30_sum(s11),
            "out_y12": sat_q15_from_q30_sum(s12),
            "out_y13": sat_q15_from_q30_sum(s13),
            "out_y14": sat_q15_from_q30_sum(s14),
        }