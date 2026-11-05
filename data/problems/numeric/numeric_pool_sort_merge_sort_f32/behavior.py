def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class MergeSort8F32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_y0 = 0
        self.out_y1 = 0
        self.out_y2 = 0
        self.out_y3 = 0
        self.out_y4 = 0
        self.out_y5 = 0
        self.out_y6 = 0
        self.out_y7 = 0

    def fp32_le(self, a, b):
        a_exp = (a >> 23) & 0xFF
        a_frac = a & 0x7FFFFF
        a_sign = (a >> 31) & 1
        b_exp = (b >> 23) & 0xFF
        b_frac = b & 0x7FFFFF
        b_sign = (b >> 31) & 1
        a_nan = (a_exp == 0xFF) and (a_frac != 0)
        b_nan = (b_exp == 0xFF) and (b_frac != 0)
        a_zero = (a & 0x7FFFFFFF) == 0
        b_zero = (b & 0x7FFFFFFF) == 0
        if a_nan or b_nan:
            return 0
        if a_zero and b_zero:
            return 1
        if a_sign != b_sign:
            return a_sign
        if a_sign == 0:
            return 1 if (a & 0x7FFFFFFF) <= (b & 0x7FFFFFFF) else 0
        return 1 if (a & 0x7FFFFFFF) >= (b & 0x7FFFFFFF) else 0

    def take_left(self, left, right, direction):
        return 1 if direction == self.fp32_le(left, right) else 0

    def merge(self, src, begin, middle, end, direction):
        left = begin
        right = middle
        dst = [0] * (end - begin)
        for k in range(end - begin):
            if left < middle and (right >= end or self.take_left(src[left], src[right], direction)):
                dst[k] = src[left]
                left += 1
            else:
                dst[k] = src[right]
                right += 1
        return dst

    def sort2(self, src, begin, direction):
        a0 = src[begin]
        a1 = src[begin + 1]
        if self.take_left(a0, a1, direction):
            return [a0, a1]
        return [a1, a0]

    def sort4(self, src, begin, direction):
        left2 = self.sort2(src, begin, direction)
        right2 = self.sort2(src, begin + 2, direction)
        merged = self.merge(left2 + right2, 0, 2, 4, direction)
        return merged

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_dir):
        src = [
            to_unsigned(in_x0, 32),
            to_unsigned(in_x1, 32),
            to_unsigned(in_x2, 32),
            to_unsigned(in_x3, 32),
            to_unsigned(in_x4, 32),
            to_unsigned(in_x5, 32),
            to_unsigned(in_x6, 32),
            to_unsigned(in_x7, 32),
        ]
        left4 = self.sort4(src, 0, in_dir)
        right4 = self.sort4(src, 4, in_dir)
        result = self.merge(left4 + right4, 0, 4, 8, in_dir)
        self.out_y0 = to_unsigned(result[0], 32)
        self.out_y1 = to_unsigned(result[1], 32)
        self.out_y2 = to_unsigned(result[2], 32)
        self.out_y3 = to_unsigned(result[3], 32)
        self.out_y4 = to_unsigned(result[4], 32)
        self.out_y5 = to_unsigned(result[5], 32)
        self.out_y6 = to_unsigned(result[6], 32)
        self.out_y7 = to_unsigned(result[7], 32)
        return {
            "out_y0": self.out_y0,
            "out_y1": self.out_y1,
            "out_y2": self.out_y2,
            "out_y3": self.out_y3,
            "out_y4": self.out_y4,
            "out_y5": self.out_y5,
            "out_y6": self.out_y6,
            "out_y7": self.out_y7,
        }