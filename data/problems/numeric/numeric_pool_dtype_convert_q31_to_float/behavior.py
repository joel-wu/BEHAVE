def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q31ToFloat32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_fp32 = 0

    def process(self, in_q31):
        x = to_signed(in_q31, 32)
        if x == 0:
            out_fp32 = 0
        else:
            sign = 1 if x < 0 else 0
            magnitude = -x if x < 0 else x
            highest = 0
            for i in range(31, -1, -1):
                if ((magnitude >> i) & 1) != 0:
                    highest = i
                    break
            exponent = highest + 96
            if highest <= 23:
                fraction = (magnitude << (23 - highest)) & 0x7FFFFF
            else:
                shift = highest - 23
                significand = magnitude >> shift
                remainder = magnitude & ((1 << shift) - 1)
                halfway = 1 << (shift - 1)
                if remainder > halfway or (remainder == halfway and (significand & 1)):
                    significand += 1
                if significand == (1 << 24):
                    significand >>= 1
                    exponent += 1
                fraction = significand & 0x7FFFFF
            out_fp32 = (sign << 31) | (exponent << 23) | fraction
        self.out_fp32 = to_unsigned(out_fp32, 32)
        return {"out_fp32": self.out_fp32}
