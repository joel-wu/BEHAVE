MASK32 = (1 << 32) - 1

K = [
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5,
    0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
    0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
    0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC,
    0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7,
    0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
    0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
    0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3,
    0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5,
    0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
    0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
]


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotr32(x, n):
    x &= MASK32
    return ((x >> n) | ((x << (32 - n)) & MASK32)) & MASK32


def ch(x, y, z):
    return ((x & y) ^ ((~x & MASK32) & z)) & MASK32


def maj(x, y, z):
    return ((x & y) ^ (x & z) ^ (y & z)) & MASK32


def big_sigma0(x):
    return (rotr32(x, 2) ^ rotr32(x, 13) ^ rotr32(x, 22)) & MASK32


def big_sigma1(x):
    return (rotr32(x, 6) ^ rotr32(x, 11) ^ rotr32(x, 25)) & MASK32


def small_sigma0(x):
    x &= MASK32
    return (rotr32(x, 7) ^ rotr32(x, 18) ^ (x >> 3)) & MASK32


def small_sigma1(x):
    x &= MASK32
    return (rotr32(x, 17) ^ rotr32(x, 19) ^ (x >> 10)) & MASK32


class Sha256BlockCompress:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_h0, in_h1, in_h2, in_h3, in_h4, in_h5, in_h6, in_h7,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15,
        in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23,
        in_b24, in_b25, in_b26, in_b27, in_b28, in_b29, in_b30, in_b31,
        in_b32, in_b33, in_b34, in_b35, in_b36, in_b37, in_b38, in_b39,
        in_b40, in_b41, in_b42, in_b43, in_b44, in_b45, in_b46, in_b47,
        in_b48, in_b49, in_b50, in_b51, in_b52, in_b53, in_b54, in_b55,
        in_b56, in_b57, in_b58, in_b59, in_b60, in_b61, in_b62, in_b63
    ):
        h0 = to_unsigned(in_h0, 32)
        h1 = to_unsigned(in_h1, 32)
        h2 = to_unsigned(in_h2, 32)
        h3 = to_unsigned(in_h3, 32)
        h4 = to_unsigned(in_h4, 32)
        h5 = to_unsigned(in_h5, 32)
        h6 = to_unsigned(in_h6, 32)
        h7 = to_unsigned(in_h7, 32)

        b = [
            to_unsigned(x, 8) for x in (
                in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
                in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15,
                in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23,
                in_b24, in_b25, in_b26, in_b27, in_b28, in_b29, in_b30, in_b31,
                in_b32, in_b33, in_b34, in_b35, in_b36, in_b37, in_b38, in_b39,
                in_b40, in_b41, in_b42, in_b43, in_b44, in_b45, in_b46, in_b47,
                in_b48, in_b49, in_b50, in_b51, in_b52, in_b53, in_b54, in_b55,
                in_b56, in_b57, in_b58, in_b59, in_b60, in_b61, in_b62, in_b63
            )
        ]

        W = [0] * 64
        for i in range(16):
            j = i * 4
            W[i] = ((b[j] << 24) | (b[j + 1] << 16) | (b[j + 2] << 8) | b[j + 3]) & MASK32
        for i in range(16, 64):
            W[i] = (small_sigma1(W[i - 2]) + W[i - 7] + small_sigma0(W[i - 15]) + W[i - 16]) & MASK32

        a = h0
        c = h2
        d = h3
        e = h4
        f = h5
        g = h6
        h = h7
        bvar = h1

        for t in range(64):
            t1 = (h + big_sigma1(e) + ch(e, f, g) + K[t] + W[t]) & MASK32
            t2 = (big_sigma0(a) + maj(a, bvar, c)) & MASK32
            a, bvar, c, d, e, f, g, h = (
                (t1 + t2) & MASK32,
                a,
                bvar,
                c,
                (d + t1) & MASK32,
                e,
                f,
                g,
            )

        return {
            "out_h0": to_unsigned(h0 + a, 32),
            "out_h1": to_unsigned(h1 + bvar, 32),
            "out_h2": to_unsigned(h2 + c, 32),
            "out_h3": to_unsigned(h3 + d, 32),
            "out_h4": to_unsigned(h4 + e, 32),
            "out_h5": to_unsigned(h5 + f, 32),
            "out_h6": to_unsigned(h6 + g, 32),
            "out_h7": to_unsigned(h7 + h, 32),
        }
