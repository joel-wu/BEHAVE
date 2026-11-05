P = [
    0xFF, 0x0F, 0x0F, 0x0C, 0x68, 0x88, 0x88, 0x80,
    0xF0, 0xFF, 0x00, 0xF3, 0x64, 0x44, 0x44, 0x40,
    0x30, 0xF0, 0xFF, 0x0F, 0x02, 0x22, 0x22, 0x26,
    0xCF, 0x00, 0xF0, 0xFF, 0x01, 0x11, 0x11, 0x16,
    0x68, 0x88, 0x88, 0x80, 0xFF, 0x0F, 0x00, 0xF3,
    0x64, 0x44, 0x44, 0x40, 0xF0, 0xFF, 0x0F, 0x0C,
    0x02, 0x22, 0x22, 0x26, 0xCF, 0x00, 0xFF, 0x0F,
    0x01, 0x11, 0x11, 0x16, 0x30, 0xF0, 0xF0, 0xFF,
]

SYNDROME_W1 = [
    0x0B, 0x3B, 0x37, 0x07, 0x19, 0x29, 0x49, 0x89,
    0x16, 0x26, 0x46, 0x86, 0x13, 0x23, 0x43, 0x83,
    0x1C, 0x2C, 0x4C, 0x8C, 0x15, 0x25, 0x45, 0x85,
    0x1A, 0x2A, 0x4A, 0x8A, 0x0D, 0xCD, 0xCE, 0x0E,
    0x70, 0x73, 0xB3, 0xB0, 0x51, 0x52, 0x54, 0x58,
    0xA1, 0xA2, 0xA4, 0xA8, 0x31, 0x32, 0x34, 0x38,
    0xC1, 0xC2, 0xC4, 0xC8, 0x61, 0x62, 0x64, 0x68,
    0x91, 0x92, 0x94, 0x98, 0xE0, 0xEC, 0xDC, 0xD0,
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80,
]


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def popcount8(x):
    x &= 0xFF
    return (
        (x & 1)
        + ((x >> 1) & 1)
        + ((x >> 2) & 1)
        + ((x >> 3) & 1)
        + ((x >> 4) & 1)
        + ((x >> 5) & 1)
        + ((x >> 6) & 1)
        + ((x >> 7) & 1)
    )


class SignalProcessingCommFecFecSecded7264:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_mode, in_data, in_codeword):
        in_mode = to_unsigned(in_mode, 1)
        in_data = to_unsigned(in_data, 64)
        in_codeword = to_unsigned(in_codeword, 72)

        if in_mode == 0:
            d0 = (in_data >> 56) & 0xFF
            d1 = (in_data >> 48) & 0xFF
            d2 = (in_data >> 40) & 0xFF
            d3 = (in_data >> 32) & 0xFF
            d4 = (in_data >> 24) & 0xFF
            d5 = (in_data >> 16) & 0xFF
            d6 = (in_data >> 8) & 0xFF
            d7 = in_data & 0xFF
            p = 0
            for i in range(8):
                t = (
                    popcount8(P[8 * i + 0] & d0)
                    + popcount8(P[8 * i + 1] & d1)
                    + popcount8(P[8 * i + 2] & d2)
                    + popcount8(P[8 * i + 3] & d3)
                    + popcount8(P[8 * i + 4] & d4)
                    + popcount8(P[8 * i + 5] & d5)
                    + popcount8(P[8 * i + 6] & d6)
                    + popcount8(P[8 * i + 7] & d7)
                )
                p = ((p << 1) | (t & 1)) & 0xFF
            out_codeword = ((p << 64) | in_data) & ((1 << 72) - 1)
            return {
                "out_codeword": to_unsigned(out_codeword, 72),
                "out_data": to_unsigned(in_data, 64),
                "out_status": 0,
            }

        r0 = (in_codeword >> 64) & 0xFF
        r1 = (in_codeword >> 56) & 0xFF
        r2 = (in_codeword >> 48) & 0xFF
        r3 = (in_codeword >> 40) & 0xFF
        r4 = (in_codeword >> 32) & 0xFF
        r5 = (in_codeword >> 24) & 0xFF
        r6 = (in_codeword >> 16) & 0xFF
        r7 = (in_codeword >> 8) & 0xFF
        r8 = in_codeword & 0xFF

        s = 0
        for i in range(8):
            b = (r0 >> (7 - i)) & 1
            t = (
                b
                + popcount8(P[8 * i + 0] & r1)
                + popcount8(P[8 * i + 1] & r2)
                + popcount8(P[8 * i + 2] & r3)
                + popcount8(P[8 * i + 3] & r4)
                + popcount8(P[8 * i + 4] & r5)
                + popcount8(P[8 * i + 5] & r6)
                + popcount8(P[8 * i + 6] & r7)
                + popcount8(P[8 * i + 7] & r8)
            )
            s = ((s << 1) | (t & 1)) & 0xFF

        if popcount8(s) == 0:
            p_corr = r0
            c0, c1, c2, c3, c4, c5, c6, c7 = r1, r2, r3, r4, r5, r6, r7, r8
            out_status = 0
        else:
            found = 0
            n_match = 0
            for n in range(72):
                if found == 0 and s == SYNDROME_W1[n]:
                    found = 1
                    n_match = n
            if found:
                e = [0, 0, 0, 0, 0, 0, 0, 0, 0]
                quot = n_match >> 3
                rem = n_match & 7
                byte_index = 8 - quot
                e[byte_index] = 1 << rem
                p_corr = r0 ^ e[0]
                c0 = r1 ^ e[1]
                c1 = r2 ^ e[2]
                c2 = r3 ^ e[3]
                c3 = r4 ^ e[4]
                c4 = r5 ^ e[5]
                c5 = r6 ^ e[6]
                c6 = r7 ^ e[7]
                c7 = r8 ^ e[8]
                out_status = 1
            else:
                p_corr = r0
                c0, c1, c2, c3, c4, c5, c6, c7 = r1, r2, r3, r4, r5, r6, r7, r8
                out_status = 2

        out_data = (
            (c0 << 56)
            | (c1 << 48)
            | (c2 << 40)
            | (c3 << 32)
            | (c4 << 24)
            | (c5 << 16)
            | (c6 << 8)
            | c7
        )
        out_codeword = (
            (p_corr << 64)
            | (c0 << 56)
            | (c1 << 48)
            | (c2 << 40)
            | (c3 << 32)
            | (c4 << 24)
            | (c5 << 16)
            | (c6 << 8)
            | c7
        )
        return {
            "out_codeword": to_unsigned(out_codeword, 72),
            "out_data": to_unsigned(out_data, 64),
            "out_status": to_unsigned(out_status, 2),
        }