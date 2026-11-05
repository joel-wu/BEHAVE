# Deterministic Q48 range reduction and degree-12 exponential polynomial.
# Integer intermediates are exact; each polynomial product truncates at Q48.
_COEFFICIENTS = (
    281474976710656, 281474976710656, 140737488355328,
    46912496118443, 11728124029611, 2345624805922, 390937467654,
    55848209665, 6981026208, 775669579, 77566958, 7051542, 587628,
)


def round_shift(value, shift):
    quotient = value >> shift
    remainder = value - (quotient << shift)
    halfway = 1 << (shift - 1)
    return quotient + int(remainder > halfway or (remainder == halfway and (quotient & 1)))


def exp_word(word):
    word &= 0xFFFFFFFF
    sign = word >> 31
    exponent = (word >> 23) & 255
    fraction = word & 0x7FFFFF
    if exponent == 255:
        if fraction:
            return 0x7FC00000
        return 0 if sign else 0x7F800000
    # These tiny inputs round to one, including signed zero/subnormals.
    if exponent <= 100:
        return 0x3F800000
    if exponent >= 134:
        return 0 if sign else 0x7F800000
    magnitude = (1 << 23) | fraction
    if exponent >= 102:
        magnitude <<= exponent - 102
    else:
        magnitude >>= 102 - exponent
    x = -magnitude if sign else magnitude
    if x >= 89 * (1 << 48):
        return 0x7F800000
    if x <= -104 * (1 << 48):
        return 0
    n = x // 195103586505167
    remainder = x - n * 195103586505167
    polynomial = _COEFFICIENTS[12]
    for j in range(11, -1, -1):
        polynomial = _COEFFICIENTS[j] + ((remainder * polynomial) >> 48)
    if n < -126:
        return round_shift(polynomial, -n - 101)
    significand = round_shift(polynomial, 25)
    if significand == (1 << 24):
        significand >>= 1
        n += 1
    if n > 127:
        return 0x7F800000
    return ((n + 127) << 23) | (significand - (1 << 23))


class VectorExp4F32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        return {'out_y0': exp_word(in_x0), 'out_y1': exp_word(in_x1),
                'out_y2': exp_word(in_x2), 'out_y3': exp_word(in_x3)}
