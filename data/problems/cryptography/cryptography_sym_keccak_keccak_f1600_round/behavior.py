MASK64 = (1 << 64) - 1

ROUND_CONSTANTS = {
    0: 0x0000000000000001,
    1: 0x0000000000008082,
    2: 0x800000000000808A,
    3: 0x8000000080008000,
    4: 0x000000000000808B,
    5: 0x0000000080000001,
    6: 0x8000000080008081,
    7: 0x8000000000008009,
    8: 0x000000000000008A,
    9: 0x0000000000000088,
    10: 0x0000000080008009,
    11: 0x000000008000000A,
    12: 0x000000008000808B,
    13: 0x800000000000008B,
    14: 0x8000000000008089,
    15: 0x8000000000008003,
    16: 0x8000000000008002,
    17: 0x8000000000000080,
    18: 0x000000000000800A,
    19: 0x800000008000000A,
    20: 0x8000000080008081,
    21: 0x8000000000008080,
    22: 0x0000000080000001,
    23: 0x8000000080008008,
}


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def ror64(v, s):
    s = s & 63
    v &= MASK64
    return ((v >> s) | ((v << ((64 - s) & 63)) & MASK64)) & MASK64


class KeccakF1600Round:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_round_idx,
        in_x0, in_x1, in_x2, in_x3, in_x4,
        in_x5, in_x6, in_x7, in_x8, in_x9,
        in_x10, in_x11, in_x12, in_x13, in_x14,
        in_x15, in_x16, in_x17, in_x18, in_x19,
        in_x20, in_x21, in_x22, in_x23, in_x24
    ):
        x = [
            in_x0 & MASK64, in_x1 & MASK64, in_x2 & MASK64, in_x3 & MASK64, in_x4 & MASK64,
            in_x5 & MASK64, in_x6 & MASK64, in_x7 & MASK64, in_x8 & MASK64, in_x9 & MASK64,
            in_x10 & MASK64, in_x11 & MASK64, in_x12 & MASK64, in_x13 & MASK64, in_x14 & MASK64,
            in_x15 & MASK64, in_x16 & MASK64, in_x17 & MASK64, in_x18 & MASK64, in_x19 & MASK64,
            in_x20 & MASK64, in_x21 & MASK64, in_x22 & MASK64, in_x23 & MASK64, in_x24 & MASK64,
        ]

        y4 = x[4] ^ x[9] ^ x[14] ^ x[19] ^ x[24]
        y1 = x[1] ^ x[6] ^ x[11] ^ x[16] ^ x[21]
        y3 = x[3] ^ x[8] ^ x[13] ^ x[18] ^ x[23]
        y0 = x[0] ^ x[5] ^ x[10] ^ x[15] ^ x[20]
        y2 = x[2] ^ x[7] ^ x[12] ^ x[17] ^ x[22]

        t = ror64(y4, 63)
        y4 = (y4 ^ ror64(y1, 63)) & MASK64
        y1 = (y1 ^ ror64(y3, 63)) & MASK64
        y3 = (y3 ^ ror64(y0, 63)) & MASK64
        y0 = (y0 ^ ror64(y2, 63)) & MASK64
        y2 = (y2 ^ t) & MASK64

        x[0] = (x[0] ^ y4) & MASK64
        x[1] = (x[1] ^ y0) & MASK64
        x[2] = (x[2] ^ y1) & MASK64
        x[3] = (x[3] ^ y2) & MASK64
        x[4] = (x[4] ^ y3) & MASK64
        x[5] = (x[5] ^ y4) & MASK64
        x[6] = (x[6] ^ y0) & MASK64
        x[7] = (x[7] ^ y1) & MASK64
        x[8] = (x[8] ^ y2) & MASK64
        x[9] = (x[9] ^ y3) & MASK64
        x[10] = (x[10] ^ y4) & MASK64
        x[11] = (x[11] ^ y0) & MASK64
        x[12] = (x[12] ^ y1) & MASK64
        x[13] = (x[13] ^ y2) & MASK64
        x[14] = (x[14] ^ y3) & MASK64
        x[15] = (x[15] ^ y4) & MASK64
        x[16] = (x[16] ^ y0) & MASK64
        x[17] = (x[17] ^ y1) & MASK64
        x[18] = (x[18] ^ y2) & MASK64
        x[19] = (x[19] ^ y3) & MASK64
        x[20] = (x[20] ^ y4) & MASK64
        x[21] = (x[21] ^ y0) & MASK64
        x[22] = (x[22] ^ y1) & MASK64
        x[23] = (x[23] ^ y2) & MASK64
        x[24] = (x[24] ^ y3) & MASK64

        t = ror64(x[1], 63)
        x[1] = ror64(x[6], 20)
        x[6] = ror64(x[9], 44)
        x[9] = ror64(x[22], 3)
        x[22] = ror64(x[14], 25)
        x[14] = ror64(x[20], 46)
        x[20] = ror64(x[2], 2)
        x[2] = ror64(x[12], 21)
        x[12] = ror64(x[13], 39)
        x[13] = ror64(x[19], 56)
        x[19] = ror64(x[23], 8)
        x[23] = ror64(x[15], 23)
        x[15] = ror64(x[4], 37)
        x[4] = ror64(x[24], 50)
        x[24] = ror64(x[21], 62)
        x[21] = ror64(x[8], 9)
        x[8] = ror64(x[16], 19)
        x[16] = ror64(x[5], 28)
        x[5] = ror64(x[3], 36)
        x[3] = ror64(x[18], 43)
        x[18] = ror64(x[17], 49)
        x[17] = ror64(x[11], 54)
        x[11] = ror64(x[7], 58)
        x[7] = ror64(x[10], 61)
        x[10] = t

        t = x[4] & ((~x[3]) & MASK64)
        x[4] = (x[4] ^ (x[1] & ((~x[0]) & MASK64))) & MASK64
        x[1] = (x[1] ^ (x[3] & ((~x[2]) & MASK64))) & MASK64
        x[3] = (x[3] ^ (x[0] & ((~x[4]) & MASK64))) & MASK64
        x[0] = (x[0] ^ (x[2] & ((~x[1]) & MASK64))) & MASK64
        x[2] = (x[2] ^ t) & MASK64

        t = x[9] & ((~x[8]) & MASK64)
        x[9] = (x[9] ^ (x[6] & ((~x[5]) & MASK64))) & MASK64
        x[6] = (x[6] ^ (x[8] & ((~x[7]) & MASK64))) & MASK64
        x[8] = (x[8] ^ (x[5] & ((~x[9]) & MASK64))) & MASK64
        x[5] = (x[5] ^ (x[7] & ((~x[6]) & MASK64))) & MASK64
        x[7] = (x[7] ^ t) & MASK64

        t = x[14] & ((~x[13]) & MASK64)
        x[14] = (x[14] ^ (x[11] & ((~x[10]) & MASK64))) & MASK64
        x[11] = (x[11] ^ (x[13] & ((~x[12]) & MASK64))) & MASK64
        x[13] = (x[13] ^ (x[10] & ((~x[14]) & MASK64))) & MASK64
        x[10] = (x[10] ^ (x[12] & ((~x[11]) & MASK64))) & MASK64
        x[12] = (x[12] ^ t) & MASK64

        t = x[19] & ((~x[18]) & MASK64)
        x[19] = (x[19] ^ (x[16] & ((~x[15]) & MASK64))) & MASK64
        x[16] = (x[16] ^ (x[18] & ((~x[17]) & MASK64))) & MASK64
        x[18] = (x[18] ^ (x[15] & ((~x[19]) & MASK64))) & MASK64
        x[15] = (x[15] ^ (x[17] & ((~x[16]) & MASK64))) & MASK64
        x[17] = (x[17] ^ t) & MASK64

        t = x[24] & ((~x[23]) & MASK64)
        x[24] = (x[24] ^ (x[21] & ((~x[20]) & MASK64))) & MASK64
        x[21] = (x[21] ^ (x[23] & ((~x[22]) & MASK64))) & MASK64
        x[23] = (x[23] ^ (x[20] & ((~x[24]) & MASK64))) & MASK64
        x[20] = (x[20] ^ (x[22] & ((~x[21]) & MASK64))) & MASK64
        x[22] = (x[22] ^ t) & MASK64

        x[0] = (x[0] ^ ROUND_CONSTANTS.get(in_round_idx, 0)) & MASK64

        return {
            "out_x0": to_unsigned(x[0], 64),
            "out_x1": to_unsigned(x[1], 64),
            "out_x2": to_unsigned(x[2], 64),
            "out_x3": to_unsigned(x[3], 64),
            "out_x4": to_unsigned(x[4], 64),
            "out_x5": to_unsigned(x[5], 64),
            "out_x6": to_unsigned(x[6], 64),
            "out_x7": to_unsigned(x[7], 64),
            "out_x8": to_unsigned(x[8], 64),
            "out_x9": to_unsigned(x[9], 64),
            "out_x10": to_unsigned(x[10], 64),
            "out_x11": to_unsigned(x[11], 64),
            "out_x12": to_unsigned(x[12], 64),
            "out_x13": to_unsigned(x[13], 64),
            "out_x14": to_unsigned(x[14], 64),
            "out_x15": to_unsigned(x[15], 64),
            "out_x16": to_unsigned(x[16], 64),
            "out_x17": to_unsigned(x[17], 64),
            "out_x18": to_unsigned(x[18], 64),
            "out_x19": to_unsigned(x[19], 64),
            "out_x20": to_unsigned(x[20], 64),
            "out_x21": to_unsigned(x[21], 64),
            "out_x22": to_unsigned(x[22], 64),
            "out_x23": to_unsigned(x[23], 64),
            "out_x24": to_unsigned(x[24], 64),
        }