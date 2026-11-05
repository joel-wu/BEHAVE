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


class KeccakChiIota:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_b0, in_b1, in_b2, in_b3, in_b4,
        in_b5, in_b6, in_b7, in_b8, in_b9,
        in_b10, in_b11, in_b12, in_b13, in_b14,
        in_b15, in_b16, in_b17, in_b18, in_b19,
        in_b20, in_b21, in_b22, in_b23, in_b24,
        in_round
    ):
        if in_round >= 24:
            return {"out_a0":0,"out_a1":0,"out_a2":0,"out_a3":0,"out_a4":0,"out_a5":0,"out_a6":0,"out_a7":0,"out_a8":0,"out_a9":0,"out_a10":0,"out_a11":0,"out_a12":0,"out_a13":0,"out_a14":0,"out_a15":0,"out_a16":0,"out_a17":0,"out_a18":0,"out_a19":0,"out_a20":0,"out_a21":0,"out_a22":0,"out_a23":0,"out_a24":0,"out_error":1}
        rc = ROUND_CONSTANTS.get(in_round, 0)

        out_a0 = (in_b0 ^ ((~in_b1 & MASK64) & in_b2) ^ rc) & MASK64
        out_a1 = (in_b1 ^ ((~in_b2 & MASK64) & in_b3)) & MASK64
        out_a2 = (in_b2 ^ ((~in_b3 & MASK64) & in_b4)) & MASK64
        out_a3 = (in_b3 ^ ((~in_b4 & MASK64) & in_b0)) & MASK64
        out_a4 = (in_b4 ^ ((~in_b0 & MASK64) & in_b1)) & MASK64

        out_a5 = (in_b5 ^ ((~in_b6 & MASK64) & in_b7)) & MASK64
        out_a6 = (in_b6 ^ ((~in_b7 & MASK64) & in_b8)) & MASK64
        out_a7 = (in_b7 ^ ((~in_b8 & MASK64) & in_b9)) & MASK64
        out_a8 = (in_b8 ^ ((~in_b9 & MASK64) & in_b5)) & MASK64
        out_a9 = (in_b9 ^ ((~in_b5 & MASK64) & in_b6)) & MASK64

        out_a10 = (in_b10 ^ ((~in_b11 & MASK64) & in_b12)) & MASK64
        out_a11 = (in_b11 ^ ((~in_b12 & MASK64) & in_b13)) & MASK64
        out_a12 = (in_b12 ^ ((~in_b13 & MASK64) & in_b14)) & MASK64
        out_a13 = (in_b13 ^ ((~in_b14 & MASK64) & in_b10)) & MASK64
        out_a14 = (in_b14 ^ ((~in_b10 & MASK64) & in_b11)) & MASK64

        out_a15 = (in_b15 ^ ((~in_b16 & MASK64) & in_b17)) & MASK64
        out_a16 = (in_b16 ^ ((~in_b17 & MASK64) & in_b18)) & MASK64
        out_a17 = (in_b17 ^ ((~in_b18 & MASK64) & in_b19)) & MASK64
        out_a18 = (in_b18 ^ ((~in_b19 & MASK64) & in_b15)) & MASK64
        out_a19 = (in_b19 ^ ((~in_b15 & MASK64) & in_b16)) & MASK64

        out_a20 = (in_b20 ^ ((~in_b21 & MASK64) & in_b22)) & MASK64
        out_a21 = (in_b21 ^ ((~in_b22 & MASK64) & in_b23)) & MASK64
        out_a22 = (in_b22 ^ ((~in_b23 & MASK64) & in_b24)) & MASK64
        out_a23 = (in_b23 ^ ((~in_b24 & MASK64) & in_b20)) & MASK64
        out_a24 = (in_b24 ^ ((~in_b20 & MASK64) & in_b21)) & MASK64

        return {"out_error": 0,
            "out_a0": to_unsigned(out_a0, 64),
            "out_a1": to_unsigned(out_a1, 64),
            "out_a2": to_unsigned(out_a2, 64),
            "out_a3": to_unsigned(out_a3, 64),
            "out_a4": to_unsigned(out_a4, 64),
            "out_a5": to_unsigned(out_a5, 64),
            "out_a6": to_unsigned(out_a6, 64),
            "out_a7": to_unsigned(out_a7, 64),
            "out_a8": to_unsigned(out_a8, 64),
            "out_a9": to_unsigned(out_a9, 64),
            "out_a10": to_unsigned(out_a10, 64),
            "out_a11": to_unsigned(out_a11, 64),
            "out_a12": to_unsigned(out_a12, 64),
            "out_a13": to_unsigned(out_a13, 64),
            "out_a14": to_unsigned(out_a14, 64),
            "out_a15": to_unsigned(out_a15, 64),
            "out_a16": to_unsigned(out_a16, 64),
            "out_a17": to_unsigned(out_a17, 64),
            "out_a18": to_unsigned(out_a18, 64),
            "out_a19": to_unsigned(out_a19, 64),
            "out_a20": to_unsigned(out_a20, 64),
            "out_a21": to_unsigned(out_a21, 64),
            "out_a22": to_unsigned(out_a22, 64),
            "out_a23": to_unsigned(out_a23, 64),
            "out_a24": to_unsigned(out_a24, 64),
        }
