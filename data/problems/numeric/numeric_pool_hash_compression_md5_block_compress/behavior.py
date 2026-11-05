def to_unsigned(v, b):
    return v & ((1 << b) - 1)


K_TABLE = [
    0xD76AA478, 0xE8C7B756, 0x242070DB, 0xC1BDCEEE,
    0xF57C0FAF, 0x4787C62A, 0xA8304613, 0xFD469501,
    0x698098D8, 0x8B44F7AF, 0xFFFF5BB1, 0x895CD7BE,
    0x6B901122, 0xFD987193, 0xA679438E, 0x49B40821,
    0xF61E2562, 0xC040B340, 0x265E5A51, 0xE9B6C7AA,
    0xD62F105D, 0x02441453, 0xD8A1E681, 0xE7D3FBC8,
    0x21E1CDE6, 0xC33707D6, 0xF4D50D87, 0x455A14ED,
    0xA9E3E905, 0xFCEFA3F8, 0x676F02D9, 0x8D2A4C8A,
    0xFFFA3942, 0x8771F681, 0x6D9D6122, 0xFDE5380C,
    0xA4BEEA44, 0x4BDECFA9, 0xF6BB4B60, 0xBEBFBC70,
    0x289B7EC6, 0xEAA127FA, 0xD4EF3085, 0x04881D05,
    0xD9D4D039, 0xE6DB99E5, 0x1FA27CF8, 0xC4AC5665,
    0xF4292244, 0x432AFF97, 0xAB9423A7, 0xFC93A039,
    0x655B59C3, 0x8F0CCC92, 0xFFEFF47D, 0x85845DD1,
    0x6FA87E4F, 0xFE2CE6E0, 0xA3014314, 0x4E0811A1,
    0xF7537E82, 0xBD3AF235, 0x2AD7D2BB, 0xEB86D391,
]

S_TABLE = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
]

G_TABLE = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    1, 6, 11, 0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12,
    5, 8, 11, 14, 1, 4, 7, 10, 13, 0, 3, 6, 9, 12, 15, 2,
    0, 7, 14, 5, 12, 3, 10, 1, 8, 15, 6, 13, 4, 11, 2, 9,
]


class Md5CompressionRoundPipeline:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_wb, in_a0, in_b0, in_c0, in_d0):
        w = [(in_wb >> (32 * i)) & 0xFFFFFFFF for i in range(16)]
        a = to_unsigned(in_a0, 32)
        b = to_unsigned(in_b0, 32)
        c = to_unsigned(in_c0, 32)
        d = to_unsigned(in_d0, 32)

        for i in range(64):
            if i < 16:
                f = (b & c) | ((~b) & d)
            elif i < 32:
                f = (d & b) | ((~d) & c)
            elif i < 48:
                f = b ^ c ^ d
            else:
                f = c ^ (b | (~d))
            f = to_unsigned(f, 32)

            t = to_unsigned(a + f + K_TABLE[i] + w[G_TABLE[i]], 32)
            s = S_TABLE[i]
            r = to_unsigned((t << s) | (t >> (32 - s)), 32)

            a_old = a
            b_old = b
            c_old = c
            d_old = d

            a = d_old
            b = to_unsigned(b_old + r, 32)
            c = b_old
            d = c_old

        return {
            "out_a64": to_unsigned(a, 32),
            "out_b64": to_unsigned(b, 32),
            "out_c64": to_unsigned(c, 32),
            "out_d64": to_unsigned(d, 32),
        }