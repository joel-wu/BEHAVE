def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Poly1305BlockStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_h0, in_h1, in_h2, in_h3, in_h4,
                in_r0, in_r1, in_r2, in_r3,
                in_s0, in_s1, in_s2, in_s3,
                in_msg, in_len):
        len_eff = in_len if in_len <= 16 else 16

        h = [
            to_unsigned(in_h0, 32),
            to_unsigned(in_h1, 32),
            to_unsigned(in_h2, 32),
            to_unsigned(in_h3, 32),
            to_unsigned(in_h4, 3),
        ]

        r = [
            to_unsigned(in_r0, 32) & 0x0FFFFFFF,
            to_unsigned(in_r1, 32) & 0x0FFFFFFC,
            to_unsigned(in_r2, 32) & 0x0FFFFFFC,
            to_unsigned(in_r3, 32) & 0x0FFFFFFC,
        ]
        rr = [to_unsigned((r[i] >> 2) * 5, 32) for i in range(4)]

        s = [
            to_unsigned(in_s0, 32),
            to_unsigned(in_s1, 32),
            to_unsigned(in_s2, 32),
            to_unsigned(in_s3, 32),
            0,
        ]

        if len_eff != 0:
            copy = [0] * 20
            msg = to_unsigned(in_msg, 128)
            for j in range(16):
                if j < len_eff:
                    copy[j] = (msg >> (8 * j)) & 0xFF
            copy[len_eff] = 1

            m = [
                copy[0] | (copy[1] << 8) | (copy[2] << 16) | (copy[3] << 24),
                copy[4] | (copy[5] << 8) | (copy[6] << 16) | (copy[7] << 24),
                copy[8] | (copy[9] << 8) | (copy[10] << 16) | (copy[11] << 24),
                copy[12] | (copy[13] << 8) | (copy[14] << 16) | (copy[15] << 24),
                copy[16] | (copy[17] << 8) | (copy[18] << 16) | (copy[19] << 24),
            ]

            sum0 = h[0] + m[0]
            h0 = to_unsigned(sum0, 32)
            carry = 1 if h0 < m[0] else 0
            sum1 = h[1] + m[1] + carry
            h1 = to_unsigned(sum1, 32)
            carry = sum1 >> 32
            sum2 = h[2] + m[2] + carry
            h2 = to_unsigned(sum2, 32)
            carry = sum2 >> 32
            sum3 = h[3] + m[3] + carry
            h3 = to_unsigned(sum3, 32)
            carry = sum3 >> 32
            sum4 = h[4] + m[4] + carry
            h4 = to_unsigned(sum4, 32)
            h = [h0, h1, h2, h3, h4]

            a0, a1, a2, a3 = r
            aa0, aa1, aa2, aa3 = rr

            x0 = a0 * h[0] + aa0 * h[4] + aa1 * h[3] + aa2 * h[2] + aa3 * h[1]
            x1 = a0 * h[1] + a1 * h[0] + aa1 * h[4] + aa2 * h[3] + aa3 * h[2]
            x2 = a0 * h[2] + a1 * h[1] + a2 * h[0] + aa2 * h[4] + aa3 * h[3]
            x3 = a0 * h[3] + a1 * h[2] + a2 * h[1] + a3 * h[0] + aa3 * h[4]
            x4 = (a0 & 3) * h[4]

            x4 = x4 + (x3 >> 32)
            x3 = to_unsigned(x3, 32)
            carry = (x4 >> 2) * 5
            x4 = x4 & 3
            x0 = x0 + carry
            h0 = to_unsigned(x0, 32)
            carry = x0 >> 32
            x1 = x1 + carry
            h1 = to_unsigned(x1, 32)
            carry = x1 >> 32
            x2 = x2 + carry
            h2 = to_unsigned(x2, 32)
            carry = x2 >> 32
            x3 = x3 + carry
            h3 = to_unsigned(x3, 32)
            carry = x3 >> 32
            x4 = x4 + carry
            h4 = x4
            h = [h0, h1, h2, h3, h4]

        t = [h[0], h[1], h[2], h[3], h[4]]
        for _ in range(2):
            g0 = to_unsigned(t[0] + 5, 32)
            carry = 1 if g0 < t[0] else 0
            g1 = to_unsigned(t[1] + carry, 32)
            carry = 1 if g1 < t[1] else 0
            g2 = to_unsigned(t[2] + carry, 32)
            carry = 1 if g2 < t[2] else 0
            g3 = to_unsigned(t[3] + carry, 32)
            carry = 1 if g3 < t[3] else 0
            g4 = to_unsigned(t[4] + carry - 4, 32)
            mask = 0xFFFFFFFF if ((g4 >> 31) & 1) == 0 else 0x00000000
            nmask = to_unsigned(~mask, 32)
            t = [
                (t[0] & nmask) ^ (g0 & mask),
                (t[1] & nmask) ^ (g1 & mask),
                (t[2] & nmask) ^ (g2 & mask),
                (t[3] & nmask) ^ (g3 & mask),
                (t[4] & nmask) ^ (g4 & mask),
            ]

        sum0 = t[0] + s[0]
        t0 = to_unsigned(sum0, 32)
        carry = 1 if t0 < s[0] else 0
        sum1 = t[1] + s[1] + carry
        t1 = to_unsigned(sum1, 32)
        carry = sum1 >> 32
        sum2 = t[2] + s[2] + carry
        t2 = to_unsigned(sum2, 32)
        carry = sum2 >> 32
        sum3 = t[3] + s[3] + carry
        t3 = to_unsigned(sum3, 32)
        carry = sum3 >> 32
        sum4 = t[4] + s[4] + carry
        t4 = to_unsigned(sum4, 32)
        t4 = 0

        return {
            "out_h0": to_unsigned(h[0], 32),
            "out_h1": to_unsigned(h[1], 32),
            "out_h2": to_unsigned(h[2], 32),
            "out_h3": to_unsigned(h[3], 32),
            "out_h4": to_unsigned(h[4], 3),
            "out_tag0": to_unsigned(t0, 32),
            "out_tag1": to_unsigned(t1, 32),
            "out_tag2": to_unsigned(t2, 32),
            "out_tag3": to_unsigned(t3, 32),
        }