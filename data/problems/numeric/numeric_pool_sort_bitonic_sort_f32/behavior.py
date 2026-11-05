def fp32_gt(a, b):
    # Ordered IEEE comparison of raw encodings, including subnormals.
    if ((a >> 23) & 255) == 255 and (a & 0x7fffff):
        return False
    if ((b >> 23) & 255) == 255 and (b & 0x7fffff):
        return False
    if (a & 0x7fffffff) == 0 and (b & 0x7fffffff) == 0:
        return False
    if (a >> 31) != (b >> 31):
        return (a >> 31) == 0
    if a >> 31:
        return (a & 0x7fffffff) < (b & 0x7fffffff)
    return (a & 0x7fffffff) > (b & 0x7fffffff)


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BitonicSort16Fp32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_x = [0] * 16

    def process(
        self,
        in_dir,
        in_x0, in_x1, in_x2, in_x3,
        in_x4, in_x5, in_x6, in_x7,
        in_x8, in_x9, in_x10, in_x11,
        in_x12, in_x13, in_x14, in_x15,
    ):
        a = [
            in_x0, in_x1, in_x2, in_x3,
            in_x4, in_x5, in_x6, in_x7,
            in_x8, in_x9, in_x10, in_x11,
            in_x12, in_x13, in_x14, in_x15,
        ]

        for n in (2, 4, 8, 16):
            for b in range(0, 16, n):
                step = n >> 1
                for k in range(step):
                    l = b + k
                    r = b + n - 1 - k
                    swap = fp32_gt(a[l], a[r]) if in_dir else fp32_gt(a[r], a[l])
                    if swap:
                        t = a[l]
                        a[l] = a[r]
                        a[r] = t
                step = n >> 2
                while step > 0:
                    for j in range(0, n, step << 1):
                        for k in range(step):
                            l = b + j + k
                            r = b + j + step + k
                            if (fp32_gt(a[l], a[r]) if in_dir else fp32_gt(a[r], a[l])):
                                t = a[l]
                                a[l] = a[r]
                                a[r] = t
                    step >>= 1

        self.out_x = a
        return {
            "out_x0": to_unsigned(a[0], 32),
            "out_x1": to_unsigned(a[1], 32),
            "out_x2": to_unsigned(a[2], 32),
            "out_x3": to_unsigned(a[3], 32),
            "out_x4": to_unsigned(a[4], 32),
            "out_x5": to_unsigned(a[5], 32),
            "out_x6": to_unsigned(a[6], 32),
            "out_x7": to_unsigned(a[7], 32),
            "out_x8": to_unsigned(a[8], 32),
            "out_x9": to_unsigned(a[9], 32),
            "out_x10": to_unsigned(a[10], 32),
            "out_x11": to_unsigned(a[11], 32),
            "out_x12": to_unsigned(a[12], 32),
            "out_x13": to_unsigned(a[13], 32),
            "out_x14": to_unsigned(a[14], 32),
            "out_x15": to_unsigned(a[15], 32),
        }
