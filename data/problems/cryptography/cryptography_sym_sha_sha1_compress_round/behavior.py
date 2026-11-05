def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SHA1CompressRound:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_h0, in_h1, in_h2, in_h3, in_h4,
        in_w0, in_w1, in_w2, in_w3, in_w4, in_w5, in_w6, in_w7,
        in_w8, in_w9, in_w10, in_w11, in_w12, in_w13, in_w14, in_w15
    ):
        W = [
            to_unsigned(in_w0, 32), to_unsigned(in_w1, 32), to_unsigned(in_w2, 32), to_unsigned(in_w3, 32),
            to_unsigned(in_w4, 32), to_unsigned(in_w5, 32), to_unsigned(in_w6, 32), to_unsigned(in_w7, 32),
            to_unsigned(in_w8, 32), to_unsigned(in_w9, 32), to_unsigned(in_w10, 32), to_unsigned(in_w11, 32),
            to_unsigned(in_w12, 32), to_unsigned(in_w13, 32), to_unsigned(in_w14, 32), to_unsigned(in_w15, 32),
        ]

        a = to_unsigned(in_h0, 32)
        b = to_unsigned(in_h1, 32)
        c = to_unsigned(in_h2, 32)
        d = to_unsigned(in_h3, 32)
        e = to_unsigned(in_h4, 32)

        for t in range(80):
            if t < 16:
                word_t = W[t]
            else:
                wt = W[(t - 3) & 15] ^ W[(t - 8) & 15] ^ W[(t - 14) & 15] ^ W[t & 15]
                wt = to_unsigned((wt << 1) | (wt >> 31), 32)
                W[t & 15] = wt
                word_t = wt

            if t < 20:
                f = (b & c) ^ (to_unsigned(~b, 32) & d)
                k = 0x5A827999
            elif t < 40:
                f = b ^ c ^ d
                k = 0x6ED9EBA1
            elif t < 60:
                f = (b & c) ^ (b & d) ^ (c & d)
                k = 0x8F1BBCDC
            else:
                f = b ^ c ^ d
                k = 0xCA62C1D6

            T = to_unsigned(to_unsigned((a << 5) | (a >> 27), 32) + f + e + k + word_t, 32)
            a, b, c, d, e = T, a, to_unsigned((b << 30) | (b >> 2), 32), c, d

        return {
            "out_h0": to_unsigned(in_h0 + a, 32),
            "out_h1": to_unsigned(in_h1 + b, 32),
            "out_h2": to_unsigned(in_h2 + c, 32),
            "out_h3": to_unsigned(in_h3 + d, 32),
            "out_h4": to_unsigned(in_h4 + e, 32),
        }