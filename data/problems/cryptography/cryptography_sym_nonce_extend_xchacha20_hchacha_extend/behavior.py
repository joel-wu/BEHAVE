def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotl32(v, n):
    v = to_unsigned(v, 32)
    return to_unsigned((v << n) | (v >> (32 - n)), 32)


def load32_le_from_int(x, word_index):
    base = 32 * word_index
    return to_unsigned((x >> base) & 0xFFFFFFFF, 32)


def store32_le_words(words):
    out = 0
    for i in range(8):
        out |= to_unsigned(words[i], 32) << (32 * i)
    return to_unsigned(out, 256)


class CryptographySymNonceExtendXchacha20HchachaExtend:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_nonce, in_key, in_const):
        x0 = load32_le_from_int(in_const, 0)
        x1 = load32_le_from_int(in_const, 1)
        x2 = load32_le_from_int(in_const, 2)
        x3 = load32_le_from_int(in_const, 3)

        x4 = load32_le_from_int(in_key, 0)
        x5 = load32_le_from_int(in_key, 1)
        x6 = load32_le_from_int(in_key, 2)
        x7 = load32_le_from_int(in_key, 3)
        x8 = load32_le_from_int(in_key, 4)
        x9 = load32_le_from_int(in_key, 5)
        x10 = load32_le_from_int(in_key, 6)
        x11 = load32_le_from_int(in_key, 7)

        x12 = load32_le_from_int(in_nonce, 0)
        x13 = load32_le_from_int(in_nonce, 1)
        x14 = load32_le_from_int(in_nonce, 2)
        x15 = load32_le_from_int(in_nonce, 3)

        for _ in range(10):
            x0 = to_unsigned(x0 + x4, 32)
            x12 = rotl32(x12 ^ x0, 16)
            x8 = to_unsigned(x8 + x12, 32)
            x4 = rotl32(x4 ^ x8, 12)
            x0 = to_unsigned(x0 + x4, 32)
            x12 = rotl32(x12 ^ x0, 8)
            x8 = to_unsigned(x8 + x12, 32)
            x4 = rotl32(x4 ^ x8, 7)

            x1 = to_unsigned(x1 + x5, 32)
            x13 = rotl32(x13 ^ x1, 16)
            x9 = to_unsigned(x9 + x13, 32)
            x5 = rotl32(x5 ^ x9, 12)
            x1 = to_unsigned(x1 + x5, 32)
            x13 = rotl32(x13 ^ x1, 8)
            x9 = to_unsigned(x9 + x13, 32)
            x5 = rotl32(x5 ^ x9, 7)

            x2 = to_unsigned(x2 + x6, 32)
            x14 = rotl32(x14 ^ x2, 16)
            x10 = to_unsigned(x10 + x14, 32)
            x6 = rotl32(x6 ^ x10, 12)
            x2 = to_unsigned(x2 + x6, 32)
            x14 = rotl32(x14 ^ x2, 8)
            x10 = to_unsigned(x10 + x14, 32)
            x6 = rotl32(x6 ^ x10, 7)

            x3 = to_unsigned(x3 + x7, 32)
            x15 = rotl32(x15 ^ x3, 16)
            x11 = to_unsigned(x11 + x15, 32)
            x7 = rotl32(x7 ^ x11, 12)
            x3 = to_unsigned(x3 + x7, 32)
            x15 = rotl32(x15 ^ x3, 8)
            x11 = to_unsigned(x11 + x15, 32)
            x7 = rotl32(x7 ^ x11, 7)

            x0 = to_unsigned(x0 + x5, 32)
            x15 = rotl32(x15 ^ x0, 16)
            x10 = to_unsigned(x10 + x15, 32)
            x5 = rotl32(x5 ^ x10, 12)
            x0 = to_unsigned(x0 + x5, 32)
            x15 = rotl32(x15 ^ x0, 8)
            x10 = to_unsigned(x10 + x15, 32)
            x5 = rotl32(x5 ^ x10, 7)

            x1 = to_unsigned(x1 + x6, 32)
            x12 = rotl32(x12 ^ x1, 16)
            x11 = to_unsigned(x11 + x12, 32)
            x6 = rotl32(x6 ^ x11, 12)
            x1 = to_unsigned(x1 + x6, 32)
            x12 = rotl32(x12 ^ x1, 8)
            x11 = to_unsigned(x11 + x12, 32)
            x6 = rotl32(x6 ^ x11, 7)

            x2 = to_unsigned(x2 + x7, 32)
            x13 = rotl32(x13 ^ x2, 16)
            x8 = to_unsigned(x8 + x13, 32)
            x7 = rotl32(x7 ^ x8, 12)
            x2 = to_unsigned(x2 + x7, 32)
            x13 = rotl32(x13 ^ x2, 8)
            x8 = to_unsigned(x8 + x13, 32)
            x7 = rotl32(x7 ^ x8, 7)

            x3 = to_unsigned(x3 + x4, 32)
            x14 = rotl32(x14 ^ x3, 16)
            x9 = to_unsigned(x9 + x14, 32)
            x4 = rotl32(x4 ^ x9, 12)
            x3 = to_unsigned(x3 + x4, 32)
            x14 = rotl32(x14 ^ x3, 8)
            x9 = to_unsigned(x9 + x14, 32)
            x4 = rotl32(x4 ^ x9, 7)

        out_subkey = store32_le_words([x0, x1, x2, x3, x12, x13, x14, x15])
        return {"out_subkey": to_unsigned(out_subkey, 256)}