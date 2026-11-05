class Q31NormalizingDivider:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_numerator, in_denominator):
        numerator = self.to_signed(in_numerator, 32)
        denominator = self.to_signed(in_denominator, 32)
        sign = (1 if numerator < 0 else 0) ^ (1 if denominator < 0 else 0)
        if denominator == 0:
            return {
                "out_quotient": 0x80000000 if sign else 0x7FFFFFFF,
                "out_shift": 0,
                "out_status_naninf": 1,
            }
        abs_numerator = self.abs_q31_sat(numerator)
        abs_denominator = self.abs_q31_sat(denominator)
        temp = (abs_numerator << 31) // abs_denominator
        high = temp >> 31
        shift_for_normalizing = 32 - self.clz32(high)
        out_shift = shift_for_normalizing if shift_for_normalizing > 0 else 0
        if shift_for_normalizing > 0:
            temp >>= shift_for_normalizing
        if sign:
            temp = -temp
        return {
            "out_quotient": self.to_unsigned(temp, 32),
            "out_shift": self.to_unsigned(out_shift, 16),
            "out_status_naninf": 0,
        }

    def to_unsigned(self, v, b):
        return v & ((1 << b) - 1)

    def to_signed(self, v, b):
        v &= (1 << b) - 1
        if v & (1 << (b - 1)):
            v -= 1 << b
        return v

    def abs_q31_sat(self, v):
        return 0x7fffffff if v == -(1 << 31) else (-v if v < 0 else v)

    def clz32(self, v):
        v &= 0xFFFFFFFF
        n = 0
        for i in range(31, -1, -1):
            if ((v >> i) & 1) != 0:
                return n
            n += 1
        return 32
