def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rol64(x, s):
    x = to_unsigned(x, 64)
    s &= 63
    return to_unsigned((x << s) | (x >> ((64 - s) & 63)), 64)


class KeccakTheta:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4,
        in_a5, in_a6, in_a7, in_a8, in_a9,
        in_a10, in_a11, in_a12, in_a13, in_a14,
        in_a15, in_a16, in_a17, in_a18, in_a19,
        in_a20, in_a21, in_a22, in_a23, in_a24
    ):
        a0 = to_unsigned(in_a0, 64)
        a1 = to_unsigned(in_a1, 64)
        a2 = to_unsigned(in_a2, 64)
        a3 = to_unsigned(in_a3, 64)
        a4 = to_unsigned(in_a4, 64)
        a5 = to_unsigned(in_a5, 64)
        a6 = to_unsigned(in_a6, 64)
        a7 = to_unsigned(in_a7, 64)
        a8 = to_unsigned(in_a8, 64)
        a9 = to_unsigned(in_a9, 64)
        a10 = to_unsigned(in_a10, 64)
        a11 = to_unsigned(in_a11, 64)
        a12 = to_unsigned(in_a12, 64)
        a13 = to_unsigned(in_a13, 64)
        a14 = to_unsigned(in_a14, 64)
        a15 = to_unsigned(in_a15, 64)
        a16 = to_unsigned(in_a16, 64)
        a17 = to_unsigned(in_a17, 64)
        a18 = to_unsigned(in_a18, 64)
        a19 = to_unsigned(in_a19, 64)
        a20 = to_unsigned(in_a20, 64)
        a21 = to_unsigned(in_a21, 64)
        a22 = to_unsigned(in_a22, 64)
        a23 = to_unsigned(in_a23, 64)
        a24 = to_unsigned(in_a24, 64)

        c0 = a0 ^ a5 ^ a10 ^ a15 ^ a20
        c1 = a1 ^ a6 ^ a11 ^ a16 ^ a21
        c2 = a2 ^ a7 ^ a12 ^ a17 ^ a22
        c3 = a3 ^ a8 ^ a13 ^ a18 ^ a23
        c4 = a4 ^ a9 ^ a14 ^ a19 ^ a24

        d0 = c4 ^ rol64(c1, 1)
        d1 = c0 ^ rol64(c2, 1)
        d2 = c1 ^ rol64(c3, 1)
        d3 = c2 ^ rol64(c4, 1)
        d4 = c3 ^ rol64(c0, 1)

        return {
            "out_a0": to_unsigned(a0 ^ d0, 64),
            "out_a1": to_unsigned(a1 ^ d1, 64),
            "out_a2": to_unsigned(a2 ^ d2, 64),
            "out_a3": to_unsigned(a3 ^ d3, 64),
            "out_a4": to_unsigned(a4 ^ d4, 64),
            "out_a5": to_unsigned(a5 ^ d0, 64),
            "out_a6": to_unsigned(a6 ^ d1, 64),
            "out_a7": to_unsigned(a7 ^ d2, 64),
            "out_a8": to_unsigned(a8 ^ d3, 64),
            "out_a9": to_unsigned(a9 ^ d4, 64),
            "out_a10": to_unsigned(a10 ^ d0, 64),
            "out_a11": to_unsigned(a11 ^ d1, 64),
            "out_a12": to_unsigned(a12 ^ d2, 64),
            "out_a13": to_unsigned(a13 ^ d3, 64),
            "out_a14": to_unsigned(a14 ^ d4, 64),
            "out_a15": to_unsigned(a15 ^ d0, 64),
            "out_a16": to_unsigned(a16 ^ d1, 64),
            "out_a17": to_unsigned(a17 ^ d2, 64),
            "out_a18": to_unsigned(a18 ^ d3, 64),
            "out_a19": to_unsigned(a19 ^ d4, 64),
            "out_a20": to_unsigned(a20 ^ d0, 64),
            "out_a21": to_unsigned(a21 ^ d1, 64),
            "out_a22": to_unsigned(a22 ^ d2, 64),
            "out_a23": to_unsigned(a23 ^ d3, 64),
            "out_a24": to_unsigned(a24 ^ d4, 64),
        }