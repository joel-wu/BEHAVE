def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


ATAN2_COEFS_Q31 = [
    0x00000000,
    0x7FFFFFFE,
    0x000001B6,
    0xD555158E,
    0x00036463,
    0x1985F617,
    0x001992AE,
    0xEED53A7F,
    0xF8F15245,
    0x2215A3A4,
    0xE0FAB004,
    0x0CDD4825,
    0xFDDBC054,
]

PIHALF_Q29 = 0x3243F6A9
PIQ29 = 0x6487ED51


class Atan2Q31:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def sat_neg_q31(self, v):
        v = to_signed(v, 32)
        if v == -(1 << 31):
            return (1 << 31) - 1
        return -v

    def clip_q63_to_q31(self, v):
        if v > (1 << 31) - 1:
            return (1 << 31) - 1
        if v < -(1 << 31):
            return -(1 << 31)
        return v

    def reduced_divide_q31(self, num, den):
        num = to_signed(num, 32)
        den = to_signed(den, 32)
        numerator = num << 31
        magnitude = abs(numerator) // abs(den)
        q = -magnitude if (numerator < 0) ^ (den < 0) else magnitude
        if q > (1 << 31) - 1:
            q = (1 << 31) - 1
        return q

    def arm_atan_limited_q31(self, x):
        x = to_signed(x, 32)
        res = to_signed(ATAN2_COEFS_Q31[12], 32)
        for i in range(1, 13):
            res = ((x * res) >> 31) + to_signed(ATAN2_COEFS_Q31[12 - i], 32)
        return self.clip_q63_to_q31(res >> 2)

    def arm_atan_q31(self, y, x):
        y = to_signed(y, 32)
        x = to_signed(x, 32)
        sign = 0
        if y < 0:
            y = self.sat_neg_q31(y)
            sign = 1 - sign
        if x < 0:
            x = self.sat_neg_q31(x)
            sign = 1 - sign
        if y > x:
            ratio = self.reduced_divide_q31(x, y)
            res = PIHALF_Q29 - self.arm_atan_limited_q31(ratio)
        else:
            ratio = self.reduced_divide_q31(y, x)
            res = self.arm_atan_limited_q31(ratio)
        if sign:
            res = self.sat_neg_q31(res)
        return to_signed(res, 32)

    def process(self, in_y, in_x):
        y = to_signed(in_y, 32)
        x = to_signed(in_x, 32)
        if x > 0:
            result = self.arm_atan_q31(y, x)
            status = 0
        elif x < 0:
            if y > 0:
                result = self.arm_atan_q31(y, x) + to_signed(PIQ29, 32)
            elif y < 0:
                result = self.arm_atan_q31(y, x) - to_signed(PIQ29, 32)
            else:
                result = to_signed(PIQ29, 32)
            status = 0
        else:
            if y > 0:
                result = to_signed(PIHALF_Q29, 32)
                status = 0
            elif y < 0:
                result = -to_signed(PIHALF_Q29, 32)
                status = 0
            else:
                result = 0
                status = 1
        return {
            "out_result": to_unsigned(result, 32),
            "out_status": to_unsigned(status, 2),
        }
