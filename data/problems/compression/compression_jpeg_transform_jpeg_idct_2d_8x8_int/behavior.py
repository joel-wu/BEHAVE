def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


CONST_BITS = 13
PASS1_BITS = 2

FIX_0_298631336 = 2446
FIX_0_390180644 = 3196
FIX_0_541196100 = 4433
FIX_0_765366865 = 6270
FIX_0_899976223 = 7373
FIX_1_175875602 = 9633
FIX_1_501321110 = 12299
FIX_1_847759065 = 15137
FIX_1_961570560 = 16069
FIX_2_053119869 = 16819
FIX_2_562915447 = 20995
FIX_3_072711026 = 25172


def descale(x, n):
    return (x + (1 << (n - 1))) >> n


def clamp_u8(v):
    if v < 0:
        return 0
    if v > 255:
        return 255
    return v


def idct_1d_pass1(x0, x1, x2, x3, x4, x5, x6, x7):
    if x1 == 0 and x2 == 0 and x3 == 0 and x4 == 0 and x5 == 0 and x6 == 0 and x7 == 0:
        dcval = x0 << PASS1_BITS
        return [dcval, dcval, dcval, dcval, dcval, dcval, dcval, dcval]

    z2 = x2
    z3 = x6
    z1 = (z2 + z3) * FIX_0_541196100
    tmp2 = z1 + z3 * (-FIX_1_847759065)
    tmp3 = z1 + z2 * FIX_0_765366865

    z2 = x0
    z3 = x4
    tmp0 = (z2 + z3) << CONST_BITS
    tmp1 = (z2 - z3) << CONST_BITS

    tmp10 = tmp0 + tmp3
    tmp13 = tmp0 - tmp3
    tmp11 = tmp1 + tmp2
    tmp12 = tmp1 - tmp2

    tmp0 = x7
    tmp1 = x5
    tmp2 = x3
    tmp3 = x1

    z1 = tmp0 + tmp3
    z2 = tmp1 + tmp2
    z3 = tmp0 + tmp2
    z4 = tmp1 + tmp3
    z5 = (z3 + z4) * FIX_1_175875602

    tmp0 = tmp0 * FIX_0_298631336
    tmp1 = tmp1 * FIX_2_053119869
    tmp2 = tmp2 * FIX_3_072711026
    tmp3 = tmp3 * FIX_1_501321110
    z1 = z1 * (-FIX_0_899976223)
    z2 = z2 * (-FIX_2_562915447)
    z3 = z3 * (-FIX_1_961570560)
    z4 = z4 * (-FIX_0_390180644)

    z3 = z3 + z5
    z4 = z4 + z5

    tmp0 = tmp0 + z1 + z3
    tmp1 = tmp1 + z2 + z4
    tmp2 = tmp2 + z2 + z3
    tmp3 = tmp3 + z1 + z4

    return [
        descale(tmp10 + tmp3, CONST_BITS - PASS1_BITS),
        descale(tmp11 + tmp2, CONST_BITS - PASS1_BITS),
        descale(tmp12 + tmp1, CONST_BITS - PASS1_BITS),
        descale(tmp13 + tmp0, CONST_BITS - PASS1_BITS),
        descale(tmp13 - tmp0, CONST_BITS - PASS1_BITS),
        descale(tmp12 - tmp1, CONST_BITS - PASS1_BITS),
        descale(tmp11 - tmp2, CONST_BITS - PASS1_BITS),
        descale(tmp10 - tmp3, CONST_BITS - PASS1_BITS),
    ]


def idct_1d_pass2(w0, w1, w2, w3, w4, w5, w6, w7):
    if w1 == 0 and w2 == 0 and w3 == 0 and w4 == 0 and w5 == 0 and w6 == 0 and w7 == 0:
        pixel = clamp_u8(descale(w0, PASS1_BITS + 3) + 128)
        return [pixel, pixel, pixel, pixel, pixel, pixel, pixel, pixel]

    z2 = w2
    z3 = w6
    z1 = (z2 + z3) * FIX_0_541196100
    tmp2 = z1 + z3 * (-FIX_1_847759065)
    tmp3 = z1 + z2 * FIX_0_765366865

    tmp0 = (w0 + w4) << CONST_BITS
    tmp1 = (w0 - w4) << CONST_BITS

    tmp10 = tmp0 + tmp3
    tmp13 = tmp0 - tmp3
    tmp11 = tmp1 + tmp2
    tmp12 = tmp1 - tmp2

    tmp0 = w7
    tmp1 = w5
    tmp2 = w3
    tmp3 = w1

    z1 = tmp0 + tmp3
    z2 = tmp1 + tmp2
    z3 = tmp0 + tmp2
    z4 = tmp1 + tmp3
    z5 = (z3 + z4) * FIX_1_175875602

    tmp0 = tmp0 * FIX_0_298631336
    tmp1 = tmp1 * FIX_2_053119869
    tmp2 = tmp2 * FIX_3_072711026
    tmp3 = tmp3 * FIX_1_501321110
    z1 = z1 * (-FIX_0_899976223)
    z2 = z2 * (-FIX_2_562915447)
    z3 = z3 * (-FIX_1_961570560)
    z4 = z4 * (-FIX_0_390180644)

    z3 = z3 + z5
    z4 = z4 + z5

    tmp0 = tmp0 + z1 + z3
    tmp1 = tmp1 + z2 + z4
    tmp2 = tmp2 + z2 + z3
    tmp3 = tmp3 + z1 + z4

    y = [
        descale(tmp10 + tmp3, CONST_BITS + PASS1_BITS + 3),
        descale(tmp11 + tmp2, CONST_BITS + PASS1_BITS + 3),
        descale(tmp12 + tmp1, CONST_BITS + PASS1_BITS + 3),
        descale(tmp13 + tmp0, CONST_BITS + PASS1_BITS + 3),
        descale(tmp13 - tmp0, CONST_BITS + PASS1_BITS + 3),
        descale(tmp12 - tmp1, CONST_BITS + PASS1_BITS + 3),
        descale(tmp11 - tmp2, CONST_BITS + PASS1_BITS + 3),
        descale(tmp10 - tmp3, CONST_BITS + PASS1_BITS + 3),
    ]
    return [clamp_u8(v + 128) for v in y]


class CompressionJpegTransformJpegIdct2D8x8Int:
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
        in_quant_0, in_quant_1, in_quant_2, in_quant_3, in_quant_4, in_quant_5, in_quant_6, in_quant_7,
        in_quant_8, in_quant_9, in_quant_10, in_quant_11, in_quant_12, in_quant_13, in_quant_14, in_quant_15,
        in_quant_16, in_quant_17, in_quant_18, in_quant_19, in_quant_20, in_quant_21, in_quant_22, in_quant_23,
        in_quant_24, in_quant_25, in_quant_26, in_quant_27, in_quant_28, in_quant_29, in_quant_30, in_quant_31,
        in_quant_32, in_quant_33, in_quant_34, in_quant_35, in_quant_36, in_quant_37, in_quant_38, in_quant_39,
        in_quant_40, in_quant_41, in_quant_42, in_quant_43, in_quant_44, in_quant_45, in_quant_46, in_quant_47,
        in_quant_48, in_quant_49, in_quant_50, in_quant_51, in_quant_52, in_quant_53, in_quant_54, in_quant_55,
        in_quant_56, in_quant_57, in_quant_58, in_quant_59, in_quant_60, in_quant_61, in_quant_62, in_quant_63
    ):
        coef = [
            to_signed(in_coef_0, 16), to_signed(in_coef_1, 16), to_signed(in_coef_2, 16), to_signed(in_coef_3, 16),
            to_signed(in_coef_4, 16), to_signed(in_coef_5, 16), to_signed(in_coef_6, 16), to_signed(in_coef_7, 16),
            to_signed(in_coef_8, 16), to_signed(in_coef_9, 16), to_signed(in_coef_10, 16), to_signed(in_coef_11, 16),
            to_signed(in_coef_12, 16), to_signed(in_coef_13, 16), to_signed(in_coef_14, 16), to_signed(in_coef_15, 16),
            to_signed(in_coef_16, 16), to_signed(in_coef_17, 16), to_signed(in_coef_18, 16), to_signed(in_coef_19, 16),
            to_signed(in_coef_20, 16), to_signed(in_coef_21, 16), to_signed(in_coef_22, 16), to_signed(in_coef_23, 16),
            to_signed(in_coef_24, 16), to_signed(in_coef_25, 16), to_signed(in_coef_26, 16), to_signed(in_coef_27, 16),
            to_signed(in_coef_28, 16), to_signed(in_coef_29, 16), to_signed(in_coef_30, 16), to_signed(in_coef_31, 16),
            to_signed(in_coef_32, 16), to_signed(in_coef_33, 16), to_signed(in_coef_34, 16), to_signed(in_coef_35, 16),
            to_signed(in_coef_36, 16), to_signed(in_coef_37, 16), to_signed(in_coef_38, 16), to_signed(in_coef_39, 16),
            to_signed(in_coef_40, 16), to_signed(in_coef_41, 16), to_signed(in_coef_42, 16), to_signed(in_coef_43, 16),
            to_signed(in_coef_44, 16), to_signed(in_coef_45, 16), to_signed(in_coef_46, 16), to_signed(in_coef_47, 16),
            to_signed(in_coef_48, 16), to_signed(in_coef_49, 16), to_signed(in_coef_50, 16), to_signed(in_coef_51, 16),
            to_signed(in_coef_52, 16), to_signed(in_coef_53, 16), to_signed(in_coef_54, 16), to_signed(in_coef_55, 16),
            to_signed(in_coef_56, 16), to_signed(in_coef_57, 16), to_signed(in_coef_58, 16), to_signed(in_coef_59, 16),
            to_signed(in_coef_60, 16), to_signed(in_coef_61, 16), to_signed(in_coef_62, 16), to_signed(in_coef_63, 16),
        ]
        quant = [
            to_unsigned(in_quant_0, 16), to_unsigned(in_quant_1, 16), to_unsigned(in_quant_2, 16), to_unsigned(in_quant_3, 16),
            to_unsigned(in_quant_4, 16), to_unsigned(in_quant_5, 16), to_unsigned(in_quant_6, 16), to_unsigned(in_quant_7, 16),
            to_unsigned(in_quant_8, 16), to_unsigned(in_quant_9, 16), to_unsigned(in_quant_10, 16), to_unsigned(in_quant_11, 16),
            to_unsigned(in_quant_12, 16), to_unsigned(in_quant_13, 16), to_unsigned(in_quant_14, 16), to_unsigned(in_quant_15, 16),
            to_unsigned(in_quant_16, 16), to_unsigned(in_quant_17, 16), to_unsigned(in_quant_18, 16), to_unsigned(in_quant_19, 16),
            to_unsigned(in_quant_20, 16), to_unsigned(in_quant_21, 16), to_unsigned(in_quant_22, 16), to_unsigned(in_quant_23, 16),
            to_unsigned(in_quant_24, 16), to_unsigned(in_quant_25, 16), to_unsigned(in_quant_26, 16), to_unsigned(in_quant_27, 16),
            to_unsigned(in_quant_28, 16), to_unsigned(in_quant_29, 16), to_unsigned(in_quant_30, 16), to_unsigned(in_quant_31, 16),
            to_unsigned(in_quant_32, 16), to_unsigned(in_quant_33, 16), to_unsigned(in_quant_34, 16), to_unsigned(in_quant_35, 16),
            to_unsigned(in_quant_36, 16), to_unsigned(in_quant_37, 16), to_unsigned(in_quant_38, 16), to_unsigned(in_quant_39, 16),
            to_unsigned(in_quant_40, 16), to_unsigned(in_quant_41, 16), to_unsigned(in_quant_42, 16), to_unsigned(in_quant_43, 16),
            to_unsigned(in_quant_44, 16), to_unsigned(in_quant_45, 16), to_unsigned(in_quant_46, 16), to_unsigned(in_quant_47, 16),
            to_unsigned(in_quant_48, 16), to_unsigned(in_quant_49, 16), to_unsigned(in_quant_50, 16), to_unsigned(in_quant_51, 16),
            to_unsigned(in_quant_52, 16), to_unsigned(in_quant_53, 16), to_unsigned(in_quant_54, 16), to_unsigned(in_quant_55, 16),
            to_unsigned(in_quant_56, 16), to_unsigned(in_quant_57, 16), to_unsigned(in_quant_58, 16), to_unsigned(in_quant_59, 16),
            to_unsigned(in_quant_60, 16), to_unsigned(in_quant_61, 16), to_unsigned(in_quant_62, 16), to_unsigned(in_quant_63, 16),
        ]
        deq = [coef[i] * quant[i] for i in range(64)]

        workspace = [0] * 64
        for c in range(8):
            col = idct_1d_pass1(
                deq[c], deq[8 + c], deq[16 + c], deq[24 + c],
                deq[32 + c], deq[40 + c], deq[48 + c], deq[56 + c]
            )
            workspace[c] = col[0]
            workspace[8 + c] = col[1]
            workspace[16 + c] = col[2]
            workspace[24 + c] = col[3]
            workspace[32 + c] = col[4]
            workspace[40 + c] = col[5]
            workspace[48 + c] = col[6]
            workspace[56 + c] = col[7]

        out = [0] * 64
        for r in range(8):
            row = idct_1d_pass2(
                workspace[8 * r + 0], workspace[8 * r + 1], workspace[8 * r + 2], workspace[8 * r + 3],
                workspace[8 * r + 4], workspace[8 * r + 5], workspace[8 * r + 6], workspace[8 * r + 7]
            )
            out[8 * r + 0] = row[0]
            out[8 * r + 1] = row[1]
            out[8 * r + 2] = row[2]
            out[8 * r + 3] = row[3]
            out[8 * r + 4] = row[4]
            out[8 * r + 5] = row[5]
            out[8 * r + 6] = row[6]
            out[8 * r + 7] = row[7]

        return {
            "out_pix_0": to_unsigned(out[0], 8),
            "out_pix_1": to_unsigned(out[1], 8),
            "out_pix_2": to_unsigned(out[2], 8),
            "out_pix_3": to_unsigned(out[3], 8),
            "out_pix_4": to_unsigned(out[4], 8),
            "out_pix_5": to_unsigned(out[5], 8),
            "out_pix_6": to_unsigned(out[6], 8),
            "out_pix_7": to_unsigned(out[7], 8),
            "out_pix_8": to_unsigned(out[8], 8),
            "out_pix_9": to_unsigned(out[9], 8),
            "out_pix_10": to_unsigned(out[10], 8),
            "out_pix_11": to_unsigned(out[11], 8),
            "out_pix_12": to_unsigned(out[12], 8),
            "out_pix_13": to_unsigned(out[13], 8),
            "out_pix_14": to_unsigned(out[14], 8),
            "out_pix_15": to_unsigned(out[15], 8),
            "out_pix_16": to_unsigned(out[16], 8),
            "out_pix_17": to_unsigned(out[17], 8),
            "out_pix_18": to_unsigned(out[18], 8),
            "out_pix_19": to_unsigned(out[19], 8),
            "out_pix_20": to_unsigned(out[20], 8),
            "out_pix_21": to_unsigned(out[21], 8),
            "out_pix_22": to_unsigned(out[22], 8),
            "out_pix_23": to_unsigned(out[23], 8),
            "out_pix_24": to_unsigned(out[24], 8),
            "out_pix_25": to_unsigned(out[25], 8),
            "out_pix_26": to_unsigned(out[26], 8),
            "out_pix_27": to_unsigned(out[27], 8),
            "out_pix_28": to_unsigned(out[28], 8),
            "out_pix_29": to_unsigned(out[29], 8),
            "out_pix_30": to_unsigned(out[30], 8),
            "out_pix_31": to_unsigned(out[31], 8),
            "out_pix_32": to_unsigned(out[32], 8),
            "out_pix_33": to_unsigned(out[33], 8),
            "out_pix_34": to_unsigned(out[34], 8),
            "out_pix_35": to_unsigned(out[35], 8),
            "out_pix_36": to_unsigned(out[36], 8),
            "out_pix_37": to_unsigned(out[37], 8),
            "out_pix_38": to_unsigned(out[38], 8),
            "out_pix_39": to_unsigned(out[39], 8),
            "out_pix_40": to_unsigned(out[40], 8),
            "out_pix_41": to_unsigned(out[41], 8),
            "out_pix_42": to_unsigned(out[42], 8),
            "out_pix_43": to_unsigned(out[43], 8),
            "out_pix_44": to_unsigned(out[44], 8),
            "out_pix_45": to_unsigned(out[45], 8),
            "out_pix_46": to_unsigned(out[46], 8),
            "out_pix_47": to_unsigned(out[47], 8),
            "out_pix_48": to_unsigned(out[48], 8),
            "out_pix_49": to_unsigned(out[49], 8),
            "out_pix_50": to_unsigned(out[50], 8),
            "out_pix_51": to_unsigned(out[51], 8),
            "out_pix_52": to_unsigned(out[52], 8),
            "out_pix_53": to_unsigned(out[53], 8),
            "out_pix_54": to_unsigned(out[54], 8),
            "out_pix_55": to_unsigned(out[55], 8),
            "out_pix_56": to_unsigned(out[56], 8),
            "out_pix_57": to_unsigned(out[57], 8),
            "out_pix_58": to_unsigned(out[58], 8),
            "out_pix_59": to_unsigned(out[59], 8),
            "out_pix_60": to_unsigned(out[60], 8),
            "out_pix_61": to_unsigned(out[61], 8),
            "out_pix_62": to_unsigned(out[62], 8),
            "out_pix_63": to_unsigned(out[63], 8),
        }