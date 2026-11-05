def rounded_integer(sig, shift, mode, sign):
    quotient = 0
    remainder = 0
    half = 1 << 27
    if shift <= 0:
        quotient = sig << (-shift)
    elif shift <= 27:
        quotient = sig >> shift
        remainder = sig & ((1 << shift) - 1)
        half = 1 << (shift - 1)
    else:
        remainder = sig
    increment = False
    if mode == 0:
        increment = remainder > half or (remainder == half and (quotient & 1) != 0)
    elif mode == 4:
        increment = remainder >= half
    elif mode == 2:
        increment = sign != 0 and remainder != 0
    elif mode == 3:
        increment = sign == 0 and remainder != 0
    if increment:
        quotient += 1
    if mode == 6 and remainder != 0:
        quotient |= 1
    return quotient, remainder


class RawFloatToRecFnRounder:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_invalidExc, in_infiniteExc, in_isNaN, in_isInf,
                in_isZero, in_sign, in_sExp, in_sig, in_roundingMode,
                in_detectTininess):
        sig = in_sig & 0x7ffffff
        exponent = in_sExp & 1023
        if exponent >= 512:
            exponent -= 1024
        sign = in_sign & 1
        mode = in_roundingMode & 7
        flags = ((in_invalidExc & 1) << 4) | ((in_infiniteExc & 1) << 3)
        result = sign << 32
        if (in_invalidExc & 1) != 0 or (in_isNaN & 1) != 0:
            result = 0xe0400000
        elif (in_infiniteExc & 1) != 0 or (in_isInf & 1) != 0:
            result |= 0xc0000000
        elif (in_isZero & 1) == 0 and sig != 0:
            top = 0
            for bit in range(27):
                if sig >= (1 << bit):
                    top = bit
            e = exponent - 281 + top
            normal_shift = top - 23
            unbounded, unused = rounded_integer(sig, normal_shift, mode, sign)
            unbounded_e = e
            if unbounded >= (1 << 24):
                unbounded_e += 1
            if unbounded_e > 127:
                flags |= 5
                if mode == 0 or mode == 4 or (mode == 2 and sign != 0) or (mode == 3 and sign == 0):
                    result |= 0xc0000000
                else:
                    result |= 0xbfffffff
            else:
                shift = normal_shift
                if e < -126:
                    shift = 132 - exponent
                q, extra = rounded_integer(sig, shift, mode, sign)
                inexact = extra != 0
                tiny = e < -126
                if (in_detectTininess & 1) != 0:
                    tiny = unbounded_e < -126
                if inexact:
                    flags |= 1
                    if tiny:
                        flags |= 2
                if e >= -126:
                    if q >= (1 << 24):
                        q >>= 1
                        e += 1
                    result |= ((e + 256) << 23) | (q & 0x7fffff)
                elif q != 0:
                    high = 0
                    for bit in range(24):
                        if q >= (1 << bit):
                            high = bit
                    result |= ((high + 107) << 23) | ((q << (23 - high)) & 0x7fffff)
        return {"out_out": result, "out_exceptionFlags": flags}
