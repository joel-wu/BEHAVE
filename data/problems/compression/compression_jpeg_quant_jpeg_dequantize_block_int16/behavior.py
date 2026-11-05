def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CompressionJpegQuantJpegDequantizeBlockInt16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_coef_0, in_coef_1, in_coef_2, in_coef_3, in_coef_4, in_coef_5, in_coef_6, in_coef_7,
        in_coef_8, in_coef_9, in_coef_10, in_coef_11, in_coef_12, in_coef_13, in_coef_14, in_coef_15,
        in_coef_16, in_coef_17, in_coef_18, in_coef_19, in_coef_20, in_coef_21, in_coef_22, in_coef_23,
        in_coef_24, in_coef_25, in_coef_26, in_coef_27, in_coef_28, in_coef_29, in_coef_30, in_coef_31,
        in_coef_32, in_coef_33, in_coef_34, in_coef_35, in_coef_36, in_coef_37, in_coef_38, in_coef_39,
        in_coef_40, in_coef_41, in_coef_42, in_coef_43, in_coef_44, in_coef_45, in_coef_46, in_coef_47,
        in_coef_48, in_coef_49, in_coef_50, in_coef_51, in_coef_52, in_coef_53, in_coef_54, in_coef_55,
        in_coef_56, in_coef_57, in_coef_58, in_coef_59, in_coef_60, in_coef_61, in_coef_62, in_coef_63,
        in_qtbl_0, in_qtbl_1, in_qtbl_2, in_qtbl_3, in_qtbl_4, in_qtbl_5, in_qtbl_6, in_qtbl_7,
        in_qtbl_8, in_qtbl_9, in_qtbl_10, in_qtbl_11, in_qtbl_12, in_qtbl_13, in_qtbl_14, in_qtbl_15,
        in_qtbl_16, in_qtbl_17, in_qtbl_18, in_qtbl_19, in_qtbl_20, in_qtbl_21, in_qtbl_22, in_qtbl_23,
        in_qtbl_24, in_qtbl_25, in_qtbl_26, in_qtbl_27, in_qtbl_28, in_qtbl_29, in_qtbl_30, in_qtbl_31,
        in_qtbl_32, in_qtbl_33, in_qtbl_34, in_qtbl_35, in_qtbl_36, in_qtbl_37, in_qtbl_38, in_qtbl_39,
        in_qtbl_40, in_qtbl_41, in_qtbl_42, in_qtbl_43, in_qtbl_44, in_qtbl_45, in_qtbl_46, in_qtbl_47,
        in_qtbl_48, in_qtbl_49, in_qtbl_50, in_qtbl_51, in_qtbl_52, in_qtbl_53, in_qtbl_54, in_qtbl_55,
        in_qtbl_56, in_qtbl_57, in_qtbl_58, in_qtbl_59, in_qtbl_60, in_qtbl_61, in_qtbl_62, in_qtbl_63
    ):
        coefs = [
            in_coef_0, in_coef_1, in_coef_2, in_coef_3, in_coef_4, in_coef_5, in_coef_6, in_coef_7,
            in_coef_8, in_coef_9, in_coef_10, in_coef_11, in_coef_12, in_coef_13, in_coef_14, in_coef_15,
            in_coef_16, in_coef_17, in_coef_18, in_coef_19, in_coef_20, in_coef_21, in_coef_22, in_coef_23,
            in_coef_24, in_coef_25, in_coef_26, in_coef_27, in_coef_28, in_coef_29, in_coef_30, in_coef_31,
            in_coef_32, in_coef_33, in_coef_34, in_coef_35, in_coef_36, in_coef_37, in_coef_38, in_coef_39,
            in_coef_40, in_coef_41, in_coef_42, in_coef_43, in_coef_44, in_coef_45, in_coef_46, in_coef_47,
            in_coef_48, in_coef_49, in_coef_50, in_coef_51, in_coef_52, in_coef_53, in_coef_54, in_coef_55,
            in_coef_56, in_coef_57, in_coef_58, in_coef_59, in_coef_60, in_coef_61, in_coef_62, in_coef_63,
        ]
        qtbls = [
            in_qtbl_0, in_qtbl_1, in_qtbl_2, in_qtbl_3, in_qtbl_4, in_qtbl_5, in_qtbl_6, in_qtbl_7,
            in_qtbl_8, in_qtbl_9, in_qtbl_10, in_qtbl_11, in_qtbl_12, in_qtbl_13, in_qtbl_14, in_qtbl_15,
            in_qtbl_16, in_qtbl_17, in_qtbl_18, in_qtbl_19, in_qtbl_20, in_qtbl_21, in_qtbl_22, in_qtbl_23,
            in_qtbl_24, in_qtbl_25, in_qtbl_26, in_qtbl_27, in_qtbl_28, in_qtbl_29, in_qtbl_30, in_qtbl_31,
            in_qtbl_32, in_qtbl_33, in_qtbl_34, in_qtbl_35, in_qtbl_36, in_qtbl_37, in_qtbl_38, in_qtbl_39,
            in_qtbl_40, in_qtbl_41, in_qtbl_42, in_qtbl_43, in_qtbl_44, in_qtbl_45, in_qtbl_46, in_qtbl_47,
            in_qtbl_48, in_qtbl_49, in_qtbl_50, in_qtbl_51, in_qtbl_52, in_qtbl_53, in_qtbl_54, in_qtbl_55,
            in_qtbl_56, in_qtbl_57, in_qtbl_58, in_qtbl_59, in_qtbl_60, in_qtbl_61, in_qtbl_62, in_qtbl_63,
        ]
        out = {}
        for i in range(64):
            prod = to_signed(coefs[i], 16) * to_unsigned(qtbls[i], 16)
            out[f"out_coef_{i}"] = to_unsigned(prod, 16)
        return out