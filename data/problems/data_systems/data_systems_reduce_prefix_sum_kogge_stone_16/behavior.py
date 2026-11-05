def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class data_systems_reduce_prefix_sum_kogge_stone_16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x0, in_x1, in_x2, in_x3,
        in_x4, in_x5, in_x6, in_x7,
        in_x8, in_x9, in_x10, in_x11,
        in_x12, in_x13, in_x14, in_x15
    ):
        s0 = [
            to_unsigned(in_x0, 32), to_unsigned(in_x1, 32),
            to_unsigned(in_x2, 32), to_unsigned(in_x3, 32),
            to_unsigned(in_x4, 32), to_unsigned(in_x5, 32),
            to_unsigned(in_x6, 32), to_unsigned(in_x7, 32),
            to_unsigned(in_x8, 32), to_unsigned(in_x9, 32),
            to_unsigned(in_x10, 32), to_unsigned(in_x11, 32),
            to_unsigned(in_x12, 32), to_unsigned(in_x13, 32),
            to_unsigned(in_x14, 32), to_unsigned(in_x15, 32),
        ]

        s1 = [
            s0[0],
            to_unsigned(s0[1] + s0[0], 32),
            to_unsigned(s0[2] + s0[1], 32),
            to_unsigned(s0[3] + s0[2], 32),
            to_unsigned(s0[4] + s0[3], 32),
            to_unsigned(s0[5] + s0[4], 32),
            to_unsigned(s0[6] + s0[5], 32),
            to_unsigned(s0[7] + s0[6], 32),
            to_unsigned(s0[8] + s0[7], 32),
            to_unsigned(s0[9] + s0[8], 32),
            to_unsigned(s0[10] + s0[9], 32),
            to_unsigned(s0[11] + s0[10], 32),
            to_unsigned(s0[12] + s0[11], 32),
            to_unsigned(s0[13] + s0[12], 32),
            to_unsigned(s0[14] + s0[13], 32),
            to_unsigned(s0[15] + s0[14], 32),
        ]

        s2 = [
            s1[0],
            s1[1],
            to_unsigned(s1[2] + s1[0], 32),
            to_unsigned(s1[3] + s1[1], 32),
            to_unsigned(s1[4] + s1[2], 32),
            to_unsigned(s1[5] + s1[3], 32),
            to_unsigned(s1[6] + s1[4], 32),
            to_unsigned(s1[7] + s1[5], 32),
            to_unsigned(s1[8] + s1[6], 32),
            to_unsigned(s1[9] + s1[7], 32),
            to_unsigned(s1[10] + s1[8], 32),
            to_unsigned(s1[11] + s1[9], 32),
            to_unsigned(s1[12] + s1[10], 32),
            to_unsigned(s1[13] + s1[11], 32),
            to_unsigned(s1[14] + s1[12], 32),
            to_unsigned(s1[15] + s1[13], 32),
        ]

        s3 = [
            s2[0],
            s2[1],
            s2[2],
            s2[3],
            to_unsigned(s2[4] + s2[0], 32),
            to_unsigned(s2[5] + s2[1], 32),
            to_unsigned(s2[6] + s2[2], 32),
            to_unsigned(s2[7] + s2[3], 32),
            to_unsigned(s2[8] + s2[4], 32),
            to_unsigned(s2[9] + s2[5], 32),
            to_unsigned(s2[10] + s2[6], 32),
            to_unsigned(s2[11] + s2[7], 32),
            to_unsigned(s2[12] + s2[8], 32),
            to_unsigned(s2[13] + s2[9], 32),
            to_unsigned(s2[14] + s2[10], 32),
            to_unsigned(s2[15] + s2[11], 32),
        ]

        s4 = [
            s3[0],
            s3[1],
            s3[2],
            s3[3],
            s3[4],
            s3[5],
            s3[6],
            s3[7],
            to_unsigned(s3[8] + s3[0], 32),
            to_unsigned(s3[9] + s3[1], 32),
            to_unsigned(s3[10] + s3[2], 32),
            to_unsigned(s3[11] + s3[3], 32),
            to_unsigned(s3[12] + s3[4], 32),
            to_unsigned(s3[13] + s3[5], 32),
            to_unsigned(s3[14] + s3[6], 32),
            to_unsigned(s3[15] + s3[7], 32),
        ]

        return {
            "out_y0": to_unsigned(s4[0], 32),
            "out_y1": to_unsigned(s4[1], 32),
            "out_y2": to_unsigned(s4[2], 32),
            "out_y3": to_unsigned(s4[3], 32),
            "out_y4": to_unsigned(s4[4], 32),
            "out_y5": to_unsigned(s4[5], 32),
            "out_y6": to_unsigned(s4[6], 32),
            "out_y7": to_unsigned(s4[7], 32),
            "out_y8": to_unsigned(s4[8], 32),
            "out_y9": to_unsigned(s4[9], 32),
            "out_y10": to_unsigned(s4[10], 32),
            "out_y11": to_unsigned(s4[11], 32),
            "out_y12": to_unsigned(s4[12], 32),
            "out_y13": to_unsigned(s4[13], 32),
            "out_y14": to_unsigned(s4[14], 32),
            "out_y15": to_unsigned(s4[15], 32),
        }