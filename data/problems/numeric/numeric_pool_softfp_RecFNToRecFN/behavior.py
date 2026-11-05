class RecodedFloatResize:
    """Round recFN(8,24) to canonical recFN(5,11), with gradual underflow."""

    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_in, in_roundingMode, in_detectTininess):
        word = in_in & 0x1FFFFFFFF
        sign = (word >> 32) & 1
        exponent = (word >> 23) & 511
        fraction = word & 0x7FFFFF
        mode = in_roundingMode & 7
        tag = exponent >> 6
        result = sign << 16
        flags = 0
        if tag == 7:
            result = 0xE200
            if (fraction & 0x400000) == 0:
                flags = 16
        elif tag == 6:
            result = (sign << 16) | 0xC000
        elif tag != 0:
            significand = 0x800000 | fraction
            e = exponent - 256
            shift = 13
            if e < -14:
                shift = 255 - exponent
            quotient = 0
            remainder = significand
            half = 0x1000000
            if shift <= 24:
                quotient = significand >> shift
                remainder = significand & ((1 << shift) - 1)
                half = 1 << (shift - 1)
            inexact = remainder != 0
            up = False
            if mode == 0:
                up = remainder > half or (remainder == half and (quotient & 1) != 0)
            elif mode == 4:
                up = remainder >= half
            elif mode == 2:
                up = sign != 0 and inexact
            elif mode == 3:
                up = sign == 0 and inexact
            if up:
                quotient += 1
            if mode == 6 and inexact:
                quotient |= 1

            # Tininess after rounding uses an unbounded exponent range, not
            # whether the final subnormal-quantized value became minimum normal.
            unbounded = significand >> 13
            extra = significand & 8191
            carry_up = False
            if mode == 0:
                carry_up = extra > 4096 or (extra == 4096 and (unbounded & 1) != 0)
            elif mode == 4:
                carry_up = extra >= 4096
            elif mode == 2:
                carry_up = sign != 0 and extra != 0
            elif mode == 3:
                carry_up = sign == 0 and extra != 0
            if carry_up:
                unbounded += 1
            tiny = e < -14
            if (in_detectTininess & 1) != 0 and e == -15 and unbounded == 2048:
                tiny = False
            if inexact:
                flags = 1
                if tiny:
                    flags |= 2

            if e >= -14:
                if quotient == 2048:
                    quotient = 1024
                    e += 1
                if e > 15:
                    flags = 5
                    magnitude = 0xBFFF
                    if mode == 0 or mode == 4 or (mode == 2 and sign != 0) or (mode == 3 and sign == 0):
                        magnitude = 0xC000
                    result |= magnitude
                else:
                    result |= ((e + 32) << 10) | (quotient & 1023)
            elif quotient != 0:
                top = 0
                for bit in range(11):
                    if quotient >= (1 << bit):
                        top = bit
                result |= ((top + 8) << 10) | ((quotient << (10 - top)) & 1023)
        return {"out_out": result, "out_exceptionFlags": flags}
