def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def rotl32(v, s):
    v = to_unsigned(v, 32)
    return to_unsigned((v << s) | (v >> (32 - s)), 32)


class CryptographySymChachaSalsa20Quarterround:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_in, in_k, in_c):
        in0 = to_unsigned(in_in >> 0, 32)
        in1 = to_unsigned(in_in >> 32, 32)
        in2 = to_unsigned(in_in >> 64, 32)
        in3 = to_unsigned(in_in >> 96, 32)

        k0 = to_unsigned(in_k >> 0, 32)
        k1 = to_unsigned(in_k >> 32, 32)
        k2 = to_unsigned(in_k >> 64, 32)
        k3 = to_unsigned(in_k >> 96, 32)
        k4 = to_unsigned(in_k >> 128, 32)
        k5 = to_unsigned(in_k >> 160, 32)
        k6 = to_unsigned(in_k >> 192, 32)
        k7 = to_unsigned(in_k >> 224, 32)

        c0 = to_unsigned(in_c >> 0, 32)
        c1 = to_unsigned(in_c >> 32, 32)
        c2 = to_unsigned(in_c >> 64, 32)
        c3 = to_unsigned(in_c >> 96, 32)

        j0 = x0 = c0
        j1 = x1 = k0
        j2 = x2 = k1
        j3 = x3 = k2
        j4 = x4 = k3
        j5 = x5 = c1
        j6 = x6 = in0
        j7 = x7 = in1
        j8 = x8 = in2
        j9 = x9 = in3
        j10 = x10 = c2
        j11 = x11 = k4
        j12 = x12 = k5
        j13 = x13 = k6
        j14 = x14 = k7
        j15 = x15 = c3

        for _ in range(10):
            x4 = to_unsigned(x4 ^ rotl32(to_unsigned(x0 + x12, 32), 7), 32)
            x8 = to_unsigned(x8 ^ rotl32(to_unsigned(x4 + x0, 32), 9), 32)
            x12 = to_unsigned(x12 ^ rotl32(to_unsigned(x8 + x4, 32), 13), 32)
            x0 = to_unsigned(x0 ^ rotl32(to_unsigned(x12 + x8, 32), 18), 32)

            x9 = to_unsigned(x9 ^ rotl32(to_unsigned(x5 + x1, 32), 7), 32)
            x13 = to_unsigned(x13 ^ rotl32(to_unsigned(x9 + x5, 32), 9), 32)
            x1 = to_unsigned(x1 ^ rotl32(to_unsigned(x13 + x9, 32), 13), 32)
            x5 = to_unsigned(x5 ^ rotl32(to_unsigned(x1 + x13, 32), 18), 32)

            x14 = to_unsigned(x14 ^ rotl32(to_unsigned(x10 + x6, 32), 7), 32)
            x2 = to_unsigned(x2 ^ rotl32(to_unsigned(x14 + x10, 32), 9), 32)
            x6 = to_unsigned(x6 ^ rotl32(to_unsigned(x2 + x14, 32), 13), 32)
            x10 = to_unsigned(x10 ^ rotl32(to_unsigned(x6 + x2, 32), 18), 32)

            x3 = to_unsigned(x3 ^ rotl32(to_unsigned(x15 + x11, 32), 7), 32)
            x7 = to_unsigned(x7 ^ rotl32(to_unsigned(x3 + x15, 32), 9), 32)
            x11 = to_unsigned(x11 ^ rotl32(to_unsigned(x7 + x3, 32), 13), 32)
            x15 = to_unsigned(x15 ^ rotl32(to_unsigned(x11 + x7, 32), 18), 32)

            x1 = to_unsigned(x1 ^ rotl32(to_unsigned(x0 + x3, 32), 7), 32)
            x2 = to_unsigned(x2 ^ rotl32(to_unsigned(x1 + x0, 32), 9), 32)
            x3 = to_unsigned(x3 ^ rotl32(to_unsigned(x2 + x1, 32), 13), 32)
            x0 = to_unsigned(x0 ^ rotl32(to_unsigned(x3 + x2, 32), 18), 32)

            x6 = to_unsigned(x6 ^ rotl32(to_unsigned(x5 + x4, 32), 7), 32)
            x7 = to_unsigned(x7 ^ rotl32(to_unsigned(x6 + x5, 32), 9), 32)
            x4 = to_unsigned(x4 ^ rotl32(to_unsigned(x7 + x6, 32), 13), 32)
            x5 = to_unsigned(x5 ^ rotl32(to_unsigned(x4 + x7, 32), 18), 32)

            x11 = to_unsigned(x11 ^ rotl32(to_unsigned(x10 + x9, 32), 7), 32)
            x8 = to_unsigned(x8 ^ rotl32(to_unsigned(x11 + x10, 32), 9), 32)
            x9 = to_unsigned(x9 ^ rotl32(to_unsigned(x8 + x11, 32), 13), 32)
            x10 = to_unsigned(x10 ^ rotl32(to_unsigned(x9 + x8, 32), 18), 32)

            x12 = to_unsigned(x12 ^ rotl32(to_unsigned(x15 + x14, 32), 7), 32)
            x13 = to_unsigned(x13 ^ rotl32(to_unsigned(x12 + x15, 32), 9), 32)
            x14 = to_unsigned(x14 ^ rotl32(to_unsigned(x13 + x12, 32), 13), 32)
            x15 = to_unsigned(x15 ^ rotl32(to_unsigned(x14 + x13, 32), 18), 32)

        y0 = to_unsigned(x0 + j0, 32)
        y1 = to_unsigned(x1 + j1, 32)
        y2 = to_unsigned(x2 + j2, 32)
        y3 = to_unsigned(x3 + j3, 32)
        y4 = to_unsigned(x4 + j4, 32)
        y5 = to_unsigned(x5 + j5, 32)
        y6 = to_unsigned(x6 + j6, 32)
        y7 = to_unsigned(x7 + j7, 32)
        y8 = to_unsigned(x8 + j8, 32)
        y9 = to_unsigned(x9 + j9, 32)
        y10 = to_unsigned(x10 + j10, 32)
        y11 = to_unsigned(x11 + j11, 32)
        y12 = to_unsigned(x12 + j12, 32)
        y13 = to_unsigned(x13 + j13, 32)
        y14 = to_unsigned(x14 + j14, 32)
        y15 = to_unsigned(x15 + j15, 32)

        out_out = (
            (y0 << 0)
            | (y1 << 32)
            | (y2 << 64)
            | (y3 << 96)
            | (y4 << 128)
            | (y5 << 160)
            | (y6 << 192)
            | (y7 << 224)
            | (y8 << 256)
            | (y9 << 288)
            | (y10 << 320)
            | (y11 << 352)
            | (y12 << 384)
            | (y13 << 416)
            | (y14 << 448)
            | (y15 << 480)
        )

        return {"out_out": to_unsigned(out_out, 512)}