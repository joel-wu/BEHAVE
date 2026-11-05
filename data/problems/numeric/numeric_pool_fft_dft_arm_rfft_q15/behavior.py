def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


A_TABLE = {
    1: (10114, -15137),
    2: (4799, -11585),
    3: (1247, -6270),
    4: (0, 0),
    5: (1247, 6270),
    6: (4799, 11585),
    7: (10114, 15137),
}

B_TABLE = {
    1: (22654, 15137),
    2: (27969, 11585),
    3: (31521, 6270),
    4: (32767, 0),
    5: (31521, -6270),
    6: (27969, -11585),
    7: (22654, -15137),
}


class RealFftSplitQ15_16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_pSrc_0, in_pSrc_1, in_pSrc_2, in_pSrc_3,
        in_pSrc_4, in_pSrc_5, in_pSrc_6, in_pSrc_7,
        in_pSrc_8, in_pSrc_9, in_pSrc_10, in_pSrc_11,
        in_pSrc_12, in_pSrc_13, in_pSrc_14, in_pSrc_15
    ):
        pSrc = [
            to_signed(in_pSrc_0, 16), to_signed(in_pSrc_1, 16),
            to_signed(in_pSrc_2, 16), to_signed(in_pSrc_3, 16),
            to_signed(in_pSrc_4, 16), to_signed(in_pSrc_5, 16),
            to_signed(in_pSrc_6, 16), to_signed(in_pSrc_7, 16),
            to_signed(in_pSrc_8, 16), to_signed(in_pSrc_9, 16),
            to_signed(in_pSrc_10, 16), to_signed(in_pSrc_11, 16),
            to_signed(in_pSrc_12, 16), to_signed(in_pSrc_13, 16),
            to_signed(in_pSrc_14, 16), to_signed(in_pSrc_15, 16),
        ]

        pDst = [0] * 18
        pDst[0] = (pSrc[0] + pSrc[1]) >> 1
        pDst[1] = 0

        for i in range(1, 8):
            src1_r = pSrc[2 * i]
            src1_i = pSrc[2 * i + 1]
            src2_r = pSrc[16 - 2 * i]
            src2_i = pSrc[17 - 2 * i]
            A_r, A_i = A_TABLE[i]
            B_r, B_i = B_TABLE[i]

            outR_acc = src1_r * A_r
            outR_acc = outR_acc - src1_i * A_i
            outR_acc = outR_acc + src2_r * B_r
            outR_acc = outR_acc + src2_i * B_i
            outR = to_signed(outR_acc, 32) >> 16

            outI_acc = src2_r * B_i
            outI_acc = outI_acc - src2_i * B_r
            outI_acc = outI_acc + src1_i * A_r
            outI_acc = outI_acc + src1_r * A_i
            outI = to_signed(outI_acc, 32) >> 16

            pDst[2 * i] = to_signed(outR, 16)
            pDst[2 * i + 1] = to_signed(outI, 16)

        pDst[16] = (pSrc[0] - pSrc[1]) >> 1
        pDst[17] = 0

        return {f"out_pDst_{i}": to_unsigned(pDst[i], 16) for i in range(18)}
