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


class CompressionJpegTransformJpegFdct2d8x8Int:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_s0, in_s1, in_s2, in_s3, in_s4, in_s5, in_s6, in_s7,
        in_s8, in_s9, in_s10, in_s11, in_s12, in_s13, in_s14, in_s15,
        in_s16, in_s17, in_s18, in_s19, in_s20, in_s21, in_s22, in_s23,
        in_s24, in_s25, in_s26, in_s27, in_s28, in_s29, in_s30, in_s31,
        in_s32, in_s33, in_s34, in_s35, in_s36, in_s37, in_s38, in_s39,
        in_s40, in_s41, in_s42, in_s43, in_s44, in_s45, in_s46, in_s47,
        in_s48, in_s49, in_s50, in_s51, in_s52, in_s53, in_s54, in_s55,
        in_s56, in_s57, in_s58, in_s59, in_s60, in_s61, in_s62, in_s63
    ):
        data = [
            to_signed(in_s0, 12), to_signed(in_s1, 12), to_signed(in_s2, 12), to_signed(in_s3, 12),
            to_signed(in_s4, 12), to_signed(in_s5, 12), to_signed(in_s6, 12), to_signed(in_s7, 12),
            to_signed(in_s8, 12), to_signed(in_s9, 12), to_signed(in_s10, 12), to_signed(in_s11, 12),
            to_signed(in_s12, 12), to_signed(in_s13, 12), to_signed(in_s14, 12), to_signed(in_s15, 12),
            to_signed(in_s16, 12), to_signed(in_s17, 12), to_signed(in_s18, 12), to_signed(in_s19, 12),
            to_signed(in_s20, 12), to_signed(in_s21, 12), to_signed(in_s22, 12), to_signed(in_s23, 12),
            to_signed(in_s24, 12), to_signed(in_s25, 12), to_signed(in_s26, 12), to_signed(in_s27, 12),
            to_signed(in_s28, 12), to_signed(in_s29, 12), to_signed(in_s30, 12), to_signed(in_s31, 12),
            to_signed(in_s32, 12), to_signed(in_s33, 12), to_signed(in_s34, 12), to_signed(in_s35, 12),
            to_signed(in_s36, 12), to_signed(in_s37, 12), to_signed(in_s38, 12), to_signed(in_s39, 12),
            to_signed(in_s40, 12), to_signed(in_s41, 12), to_signed(in_s42, 12), to_signed(in_s43, 12),
            to_signed(in_s44, 12), to_signed(in_s45, 12), to_signed(in_s46, 12), to_signed(in_s47, 12),
            to_signed(in_s48, 12), to_signed(in_s49, 12), to_signed(in_s50, 12), to_signed(in_s51, 12),
            to_signed(in_s52, 12), to_signed(in_s53, 12), to_signed(in_s54, 12), to_signed(in_s55, 12),
            to_signed(in_s56, 12), to_signed(in_s57, 12), to_signed(in_s58, 12), to_signed(in_s59, 12),
            to_signed(in_s60, 12), to_signed(in_s61, 12), to_signed(in_s62, 12), to_signed(in_s63, 12),
        ]

        for r in range(8):
            base = 8 * r
            tmp0 = data[base + 0] + data[base + 7]
            tmp7 = data[base + 0] - data[base + 7]
            tmp1 = data[base + 1] + data[base + 6]
            tmp6 = data[base + 1] - data[base + 6]
            tmp2 = data[base + 2] + data[base + 5]
            tmp5 = data[base + 2] - data[base + 5]
            tmp3 = data[base + 3] + data[base + 4]
            tmp4 = data[base + 3] - data[base + 4]

            tmp10 = tmp0 + tmp3
            tmp13 = tmp0 - tmp3
            tmp11 = tmp1 + tmp2
            tmp12 = tmp1 - tmp2

            data[base + 0] = (tmp10 + tmp11) << PASS1_BITS
            data[base + 4] = (tmp10 - tmp11) << PASS1_BITS

            z1 = (tmp12 + tmp13) * FIX_0_541196100
            data[base + 2] = descale(z1 + tmp13 * FIX_0_765366865, CONST_BITS - PASS1_BITS)
            data[base + 6] = descale(z1 + tmp12 * (-FIX_1_847759065), CONST_BITS - PASS1_BITS)

            z1 = tmp4 + tmp7
            z2 = tmp5 + tmp6
            z3 = tmp4 + tmp6
            z4 = tmp5 + tmp7
            z5 = (z3 + z4) * FIX_1_175875602

            tmp4 = tmp4 * FIX_0_298631336
            tmp5 = tmp5 * FIX_2_053119869
            tmp6 = tmp6 * FIX_3_072711026
            tmp7 = tmp7 * FIX_1_501321110
            z1 = z1 * (-FIX_0_899976223)
            z2 = z2 * (-FIX_2_562915447)
            z3 = z3 * (-FIX_1_961570560)
            z4 = z4 * (-FIX_0_390180644)

            z3 += z5
            z4 += z5

            data[base + 7] = descale(tmp4 + z1 + z3, CONST_BITS - PASS1_BITS)
            data[base + 5] = descale(tmp5 + z2 + z4, CONST_BITS - PASS1_BITS)
            data[base + 3] = descale(tmp6 + z2 + z3, CONST_BITS - PASS1_BITS)
            data[base + 1] = descale(tmp7 + z1 + z4, CONST_BITS - PASS1_BITS)

        for c in range(8):
            tmp0 = data[8 * 0 + c] + data[8 * 7 + c]
            tmp7 = data[8 * 0 + c] - data[8 * 7 + c]
            tmp1 = data[8 * 1 + c] + data[8 * 6 + c]
            tmp6 = data[8 * 1 + c] - data[8 * 6 + c]
            tmp2 = data[8 * 2 + c] + data[8 * 5 + c]
            tmp5 = data[8 * 2 + c] - data[8 * 5 + c]
            tmp3 = data[8 * 3 + c] + data[8 * 4 + c]
            tmp4 = data[8 * 3 + c] - data[8 * 4 + c]

            tmp10 = tmp0 + tmp3
            tmp13 = tmp0 - tmp3
            tmp11 = tmp1 + tmp2
            tmp12 = tmp1 - tmp2

            data[8 * 0 + c] = descale(tmp10 + tmp11, PASS1_BITS)
            data[8 * 4 + c] = descale(tmp10 - tmp11, PASS1_BITS)

            z1 = (tmp12 + tmp13) * FIX_0_541196100
            data[8 * 2 + c] = descale(z1 + tmp13 * FIX_0_765366865, CONST_BITS + PASS1_BITS)
            data[8 * 6 + c] = descale(z1 + tmp12 * (-FIX_1_847759065), CONST_BITS + PASS1_BITS)

            z1 = tmp4 + tmp7
            z2 = tmp5 + tmp6
            z3 = tmp4 + tmp6
            z4 = tmp5 + tmp7
            z5 = (z3 + z4) * FIX_1_175875602

            tmp4 = tmp4 * FIX_0_298631336
            tmp5 = tmp5 * FIX_2_053119869
            tmp6 = tmp6 * FIX_3_072711026
            tmp7 = tmp7 * FIX_1_501321110
            z1 = z1 * (-FIX_0_899976223)
            z2 = z2 * (-FIX_2_562915447)
            z3 = z3 * (-FIX_1_961570560)
            z4 = z4 * (-FIX_0_390180644)

            z3 += z5
            z4 += z5

            data[8 * 7 + c] = descale(tmp4 + z1 + z3, CONST_BITS + PASS1_BITS)
            data[8 * 5 + c] = descale(tmp5 + z2 + z4, CONST_BITS + PASS1_BITS)
            data[8 * 3 + c] = descale(tmp6 + z2 + z3, CONST_BITS + PASS1_BITS)
            data[8 * 1 + c] = descale(tmp7 + z1 + z4, CONST_BITS + PASS1_BITS)

        return {f"out_d{i}": to_unsigned(data[i], 16) for i in range(64)}