def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotl32(x, n):
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


class ChaCha20Block:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_key, in_nonce_size, in_nonce, in_block_counter):
        if in_nonce_size == 3:
            return {
                "out_block": 0,
                "out_next_state_12_15": 0,
                "out_error": 1,
            }

        key_byte = [(in_key >> (8 * j)) & 0xFF for j in range(32)]
        nonce_byte = [(in_nonce >> (8 * j)) & 0xFF for j in range(16)]

        def le_word(B, i):
            j = 4 * i
            return (
                B[j + 0]
                | (B[j + 1] << 8)
                | (B[j + 2] << 16)
                | (B[j + 3] << 24)
            ) & 0xFFFFFFFF

        s = [0] * 16
        s[0] = 0x61707865
        s[1] = 0x3320646E
        s[2] = 0x79622D32
        s[3] = 0x6B206574
        for i in range(8):
            s[4 + i] = le_word(key_byte, i)

        if in_nonce_size == 0:
            s[12] = in_block_counter & 0xFFFFFFFF
            s[13] = (in_block_counter >> 32) & 0xFFFFFFFF
            s[14] = le_word(nonce_byte, 0)
            s[15] = le_word(nonce_byte, 1)
        elif in_nonce_size == 1:
            s[12] = in_block_counter & 0xFFFFFFFF
            s[13] = le_word(nonce_byte, 0)
            s[14] = le_word(nonce_byte, 1)
            s[15] = le_word(nonce_byte, 2)
        else:
            s[12] = le_word(nonce_byte, 0)
            s[13] = le_word(nonce_byte, 1)
            s[14] = le_word(nonce_byte, 2)
            s[15] = le_word(nonce_byte, 3)

        h = s[:]

        def qr(a, b, c, d):
            a = (a + b) & 0xFFFFFFFF
            d ^= a
            d = rotl32(d, 16)
            c = (c + d) & 0xFFFFFFFF
            b ^= c
            b = rotl32(b, 12)
            a = (a + b) & 0xFFFFFFFF
            d ^= a
            d = rotl32(d, 8)
            c = (c + d) & 0xFFFFFFFF
            b ^= c
            b = rotl32(b, 7)
            return a, b, c, d

        for _ in range(10):
            h[0], h[4], h[8], h[12] = qr(h[0], h[4], h[8], h[12])
            h[1], h[5], h[9], h[13] = qr(h[1], h[5], h[9], h[13])
            h[2], h[6], h[10], h[14] = qr(h[2], h[6], h[10], h[14])
            h[3], h[7], h[11], h[15] = qr(h[3], h[7], h[11], h[15])
            h[0], h[5], h[10], h[15] = qr(h[0], h[5], h[10], h[15])
            h[1], h[6], h[11], h[12] = qr(h[1], h[6], h[11], h[12])
            h[2], h[7], h[8], h[13] = qr(h[2], h[7], h[8], h[13])
            h[3], h[4], h[9], h[14] = qr(h[3], h[4], h[9], h[14])

        y = [to_unsigned(h[i] + s[i], 32) for i in range(16)]

        out_block = 0
        for i in range(16):
            w = y[i]
            out_block |= ((w >> 0) & 0xFF) << (8 * (4 * i + 0))
            out_block |= ((w >> 8) & 0xFF) << (8 * (4 * i + 1))
            out_block |= ((w >> 16) & 0xFF) << (8 * (4 * i + 2))
            out_block |= ((w >> 24) & 0xFF) << (8 * (4 * i + 3))

        if in_nonce_size == 0:
            next12 = to_unsigned(s[12] + 1, 32)
            if next12 != 0:
                next13 = s[13]
            else:
                next13 = to_unsigned(s[13] + 1, 32)
            next14 = s[14]
            next15 = s[15]
            out_error = 1 if next12 == 0 and next13 == 0 else 0
        elif in_nonce_size == 1:
            next12 = to_unsigned(s[12] + 1, 32)
            next13 = s[13]
            next14 = s[14]
            next15 = s[15]
            out_error = 1 if next12 == 0 else 0
        else:
            next12 = s[12]
            next13 = s[13]
            next14 = s[14]
            next15 = s[15]
            out_error = 0

        out_next_state_12_15 = (
            next12
            | (next13 << 32)
            | (next14 << 64)
            | (next15 << 96)
        )

        return {
            "out_block": to_unsigned(out_block, 512),
            "out_next_state_12_15": to_unsigned(out_next_state_12_15, 128),
            "out_error": out_error,
        }