NATURAL_ORDER = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CompressionJpegTransformJpegInverseZigzagScan:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_coeff_0, in_coeff_1, in_coeff_2, in_coeff_3,
        in_coeff_4, in_coeff_5, in_coeff_6, in_coeff_7,
        in_coeff_8, in_coeff_9, in_coeff_10, in_coeff_11,
        in_coeff_12, in_coeff_13, in_coeff_14, in_coeff_15,
        in_coeff_16, in_coeff_17, in_coeff_18, in_coeff_19,
        in_coeff_20, in_coeff_21, in_coeff_22, in_coeff_23,
        in_coeff_24, in_coeff_25, in_coeff_26, in_coeff_27,
        in_coeff_28, in_coeff_29, in_coeff_30, in_coeff_31,
        in_coeff_32, in_coeff_33, in_coeff_34, in_coeff_35,
        in_coeff_36, in_coeff_37, in_coeff_38, in_coeff_39,
        in_coeff_40, in_coeff_41, in_coeff_42, in_coeff_43,
        in_coeff_44, in_coeff_45, in_coeff_46, in_coeff_47,
        in_coeff_48, in_coeff_49, in_coeff_50, in_coeff_51,
        in_coeff_52, in_coeff_53, in_coeff_54, in_coeff_55,
        in_coeff_56, in_coeff_57, in_coeff_58, in_coeff_59,
        in_coeff_60, in_coeff_61, in_coeff_62, in_coeff_63
    ):
        in_coeff = [
            in_coeff_0, in_coeff_1, in_coeff_2, in_coeff_3,
            in_coeff_4, in_coeff_5, in_coeff_6, in_coeff_7,
            in_coeff_8, in_coeff_9, in_coeff_10, in_coeff_11,
            in_coeff_12, in_coeff_13, in_coeff_14, in_coeff_15,
            in_coeff_16, in_coeff_17, in_coeff_18, in_coeff_19,
            in_coeff_20, in_coeff_21, in_coeff_22, in_coeff_23,
            in_coeff_24, in_coeff_25, in_coeff_26, in_coeff_27,
            in_coeff_28, in_coeff_29, in_coeff_30, in_coeff_31,
            in_coeff_32, in_coeff_33, in_coeff_34, in_coeff_35,
            in_coeff_36, in_coeff_37, in_coeff_38, in_coeff_39,
            in_coeff_40, in_coeff_41, in_coeff_42, in_coeff_43,
            in_coeff_44, in_coeff_45, in_coeff_46, in_coeff_47,
            in_coeff_48, in_coeff_49, in_coeff_50, in_coeff_51,
            in_coeff_52, in_coeff_53, in_coeff_54, in_coeff_55,
            in_coeff_56, in_coeff_57, in_coeff_58, in_coeff_59,
            in_coeff_60, in_coeff_61, in_coeff_62, in_coeff_63,
        ]
        out_coeff = [0] * 64
        for k in range(64):
            out_coeff[NATURAL_ORDER[k]] = in_coeff[k]
        return {f"out_coeff_{i}": to_unsigned(out_coeff[i], 16) for i in range(64)}