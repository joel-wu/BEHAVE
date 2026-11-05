from fp_lib import fp32_add


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class PQLUTScan:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_lut_0,
        in_lut_1,
        in_lut_2,
        in_lut_3,
        in_lut_4,
        in_lut_5,
        in_lut_6,
        in_lut_7,
        in_lut_8,
        in_lut_9,
        in_lut_10,
        in_lut_11,
        in_lut_12,
        in_lut_13,
        in_lut_14,
        in_lut_15,
        in_code_0,
        in_code_1,
        in_code_2,
        in_code_3,
    ):
        lut = [
            in_lut_0,
            in_lut_1,
            in_lut_2,
            in_lut_3,
            in_lut_4,
            in_lut_5,
            in_lut_6,
            in_lut_7,
            in_lut_8,
            in_lut_9,
            in_lut_10,
            in_lut_11,
            in_lut_12,
            in_lut_13,
            in_lut_14,
            in_lut_15,
        ]
        c0 = in_code_0 & 0x3
        c1 = in_code_1 & 0x3
        c2 = in_code_2 & 0x3
        c3 = in_code_3 & 0x3
        s0 = lut[c0]
        s1 = lut[4 + c1]
        s2 = lut[8 + c2]
        s3 = lut[12 + c3]
        out_distance = fp32_add(fp32_add(fp32_add(fp32_add(0, s0), s1), s2), s3)
        return {"out_distance": to_unsigned(out_distance, 32)}
