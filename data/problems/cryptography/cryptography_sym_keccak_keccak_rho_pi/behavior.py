MASK64 = (1 << 64) - 1
MASK1600 = (1 << 1600) - 1

ROUND_CONSTANTS = [
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
]


def rol64(x, s):
    return ((x << s) | (x >> (64 - s))) & MASK64


class KeccakPermutation1600:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, in_state, in_rounds):
        in_rounds &= 255
        if in_rounds not in (12, 24):
            return {"out_state": 0, "out_error": 1}
        a0 = (in_state >> 0) & MASK64
        a1 = (in_state >> 64) & MASK64
        a2 = (in_state >> 128) & MASK64
        a3 = (in_state >> 192) & MASK64
        a4 = (in_state >> 256) & MASK64
        a5 = (in_state >> 320) & MASK64
        a6 = (in_state >> 384) & MASK64
        a7 = (in_state >> 448) & MASK64
        a8 = (in_state >> 512) & MASK64
        a9 = (in_state >> 576) & MASK64
        a10 = (in_state >> 640) & MASK64
        a11 = (in_state >> 704) & MASK64
        a12 = (in_state >> 768) & MASK64
        a13 = (in_state >> 832) & MASK64
        a14 = (in_state >> 896) & MASK64
        a15 = (in_state >> 960) & MASK64
        a16 = (in_state >> 1024) & MASK64
        a17 = (in_state >> 1088) & MASK64
        a18 = (in_state >> 1152) & MASK64
        a19 = (in_state >> 1216) & MASK64
        a20 = (in_state >> 1280) & MASK64
        a21 = (in_state >> 1344) & MASK64
        a22 = (in_state >> 1408) & MASK64
        a23 = (in_state >> 1472) & MASK64
        a24 = (in_state >> 1536) & MASK64

        start_round = 0 if in_rounds == 24 else 12

        for i in range(start_round, 24):
            c0 = a0 ^ a5 ^ a10 ^ a15 ^ a20
            c1 = a1 ^ a6 ^ a11 ^ a16 ^ a21
            c2 = a2 ^ a7 ^ a12 ^ a17 ^ a22
            c3 = a3 ^ a8 ^ a13 ^ a18 ^ a23
            c4 = a4 ^ a9 ^ a14 ^ a19 ^ a24

            d = (c4 ^ rol64(c1, 1)) & MASK64
            b0 = (d ^ a0) & MASK64
            b16 = rol64((d ^ a5) & MASK64, 36)
            b7 = rol64((d ^ a10) & MASK64, 3)
            b23 = rol64((d ^ a15) & MASK64, 41)
            b14 = rol64((d ^ a20) & MASK64, 18)

            d = (c0 ^ rol64(c2, 1)) & MASK64
            b10 = rol64((d ^ a1) & MASK64, 1)
            b1 = rol64((d ^ a6) & MASK64, 44)
            b17 = rol64((d ^ a11) & MASK64, 10)
            b8 = rol64((d ^ a16) & MASK64, 45)
            b24 = rol64((d ^ a21) & MASK64, 2)

            d = (c1 ^ rol64(c3, 1)) & MASK64
            b20 = rol64((d ^ a2) & MASK64, 62)
            b11 = rol64((d ^ a7) & MASK64, 6)
            b2 = rol64((d ^ a12) & MASK64, 43)
            b18 = rol64((d ^ a17) & MASK64, 15)
            b9 = rol64((d ^ a22) & MASK64, 61)

            d = (c2 ^ rol64(c4, 1)) & MASK64
            b5 = rol64((d ^ a3) & MASK64, 28)
            b21 = rol64((d ^ a8) & MASK64, 55)
            b12 = rol64((d ^ a13) & MASK64, 25)
            b3 = rol64((d ^ a18) & MASK64, 21)
            b19 = rol64((d ^ a23) & MASK64, 56)

            d = (c3 ^ rol64(c0, 1)) & MASK64
            b15 = rol64((d ^ a4) & MASK64, 27)
            b6 = rol64((d ^ a9) & MASK64, 20)
            b22 = rol64((d ^ a14) & MASK64, 39)
            b13 = rol64((d ^ a19) & MASK64, 8)
            b4 = rol64((d ^ a24) & MASK64, 14)

            a0 = (b0 ^ (((~b1) & MASK64) & b2) ^ ROUND_CONSTANTS[i]) & MASK64
            a1 = (b1 ^ (((~b2) & MASK64) & b3)) & MASK64
            a2 = (b2 ^ (((~b3) & MASK64) & b4)) & MASK64
            a3 = (b3 ^ (((~b4) & MASK64) & b0)) & MASK64
            a4 = (b4 ^ (((~b0) & MASK64) & b1)) & MASK64

            a5 = (b5 ^ (((~b6) & MASK64) & b7)) & MASK64
            a6 = (b6 ^ (((~b7) & MASK64) & b8)) & MASK64
            a7 = (b7 ^ (((~b8) & MASK64) & b9)) & MASK64
            a8 = (b8 ^ (((~b9) & MASK64) & b5)) & MASK64
            a9 = (b9 ^ (((~b5) & MASK64) & b6)) & MASK64

            a10 = (b10 ^ (((~b11) & MASK64) & b12)) & MASK64
            a11 = (b11 ^ (((~b12) & MASK64) & b13)) & MASK64
            a12 = (b12 ^ (((~b13) & MASK64) & b14)) & MASK64
            a13 = (b13 ^ (((~b14) & MASK64) & b10)) & MASK64
            a14 = (b14 ^ (((~b10) & MASK64) & b11)) & MASK64

            a15 = (b15 ^ (((~b16) & MASK64) & b17)) & MASK64
            a16 = (b16 ^ (((~b17) & MASK64) & b18)) & MASK64
            a17 = (b17 ^ (((~b18) & MASK64) & b19)) & MASK64
            a18 = (b18 ^ (((~b19) & MASK64) & b15)) & MASK64
            a19 = (b19 ^ (((~b15) & MASK64) & b16)) & MASK64

            a20 = (b20 ^ (((~b21) & MASK64) & b22)) & MASK64
            a21 = (b21 ^ (((~b22) & MASK64) & b23)) & MASK64
            a22 = (b22 ^ (((~b23) & MASK64) & b24)) & MASK64
            a23 = (b23 ^ (((~b24) & MASK64) & b20)) & MASK64
            a24 = (b24 ^ (((~b20) & MASK64) & b21)) & MASK64

        out_state = (
            (a0 << 0)
            | (a1 << 64)
            | (a2 << 128)
            | (a3 << 192)
            | (a4 << 256)
            | (a5 << 320)
            | (a6 << 384)
            | (a7 << 448)
            | (a8 << 512)
            | (a9 << 576)
            | (a10 << 640)
            | (a11 << 704)
            | (a12 << 768)
            | (a13 << 832)
            | (a14 << 896)
            | (a15 << 960)
            | (a16 << 1024)
            | (a17 << 1088)
            | (a18 << 1152)
            | (a19 << 1216)
            | (a20 << 1280)
            | (a21 << 1344)
            | (a22 << 1408)
            | (a23 << 1472)
            | (a24 << 1536)
        ) & MASK1600

        return {"out_state": out_state, "out_error": 0}
