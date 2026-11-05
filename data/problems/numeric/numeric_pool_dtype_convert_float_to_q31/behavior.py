def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class FloatToQ31:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_q31 = 0

    def process(self, in_x):
        sign = (in_x >> 31) & 1
        exp = (in_x >> 23) & 0xFF
        frac = in_x & 0x7FFFFF

        if exp == 0xFF:
            if frac != 0:
                result = 0
            else:
                result = 0x80000000 if sign else 0x7FFFFFFF
        elif exp == 0x00:
            result = 0
        else:
            e = exp - 127
            mant = (1 << 23) | frac
            shift = e + 8
            if shift >= 0:
                mag = mant << shift
            else:
                mag = mant >> (-shift)

            if sign == 0:
                result = 0x7FFFFFFF if mag >= 0x80000000 else mag
            else:
                result = 0x80000000 if mag >= 0x80000000 else to_unsigned(-mag, 32)

        self.out_q31 = to_unsigned(result, 32)
        return {"out_q31": self.out_q31}