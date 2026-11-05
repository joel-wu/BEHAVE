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


class Fp32VectorMin16:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_min = 0
        self.out_index = 0

    def process(
        self,
        in_x0, in_x1, in_x2, in_x3,
        in_x4, in_x5, in_x6, in_x7,
        in_x8, in_x9, in_x10, in_x11,
        in_x12, in_x13, in_x14, in_x15
    ):
        x = [
            in_x0, in_x1, in_x2, in_x3,
            in_x4, in_x5, in_x6, in_x7,
            in_x8, in_x9, in_x10, in_x11,
            in_x12, in_x13, in_x14, in_x15,
        ]

        current_min = x[0]
        current_index = 0

        if fp32_gt(current_min, x[1]):
            current_min = x[1]
            current_index = 1
        if fp32_gt(current_min, x[2]):
            current_min = x[2]
            current_index = 2
        if fp32_gt(current_min, x[3]):
            current_min = x[3]
            current_index = 3
        if fp32_gt(current_min, x[4]):
            current_min = x[4]
            current_index = 4
        if fp32_gt(current_min, x[5]):
            current_min = x[5]
            current_index = 5
        if fp32_gt(current_min, x[6]):
            current_min = x[6]
            current_index = 6
        if fp32_gt(current_min, x[7]):
            current_min = x[7]
            current_index = 7
        if fp32_gt(current_min, x[8]):
            current_min = x[8]
            current_index = 8
        if fp32_gt(current_min, x[9]):
            current_min = x[9]
            current_index = 9
        if fp32_gt(current_min, x[10]):
            current_min = x[10]
            current_index = 10
        if fp32_gt(current_min, x[11]):
            current_min = x[11]
            current_index = 11
        if fp32_gt(current_min, x[12]):
            current_min = x[12]
            current_index = 12
        if fp32_gt(current_min, x[13]):
            current_min = x[13]
            current_index = 13
        if fp32_gt(current_min, x[14]):
            current_min = x[14]
            current_index = 14
        if fp32_gt(current_min, x[15]):
            current_min = x[15]
            current_index = 15

        self.out_min = to_unsigned(current_min, 32)
        self.out_index = to_unsigned(current_index, 4)

        return {"out_min": self.out_min, "out_index": self.out_index}
