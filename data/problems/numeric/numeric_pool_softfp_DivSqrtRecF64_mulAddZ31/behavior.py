def rounded_integer(sig, shift, mode, sign):
    quotient = 0
    remainder = 0
    half = 1 << 56
    if shift <= 0:
        quotient = sig << (-shift)
    elif shift <= 56:
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


def pack_rounded(sig, exponent, mode, sign, detect_tininess):
    # The leading raw bit is 54: magnitude = sig * 2**(exponent-2102).
    q, extra = rounded_integer(sig, 2, mode, sign)
    e = exponent - 2048
    ue = e
    if q >= (1 << 53):
        ue += 1
    result = sign << 64
    flags = 0
    if ue > 1023:
        flags = 5
        if mode == 0 or mode == 4 or (mode == 2 and sign != 0) or (mode == 3 and sign == 0):
            result |= 0xc000000000000000
        else:
            result |= 0xbfffffffffffffff
    else:
        shift = 2
        if e < -1022:
            shift = 1028 - exponent
        q, extra = rounded_integer(sig, shift, mode, sign)
        tiny = e < -1022
        if detect_tininess != 0:
            tiny = ue < -1022
        if extra != 0:
            flags = 1
            if tiny:
                flags |= 2
        if e >= -1022:
            if q >= (1 << 53):
                q >>= 1
                e += 1
            result |= ((e + 2048) << 52) | (q & 0xfffffffffffff)
        elif q != 0:
            top = 0
            for bit in range(53):
                if q >= (1 << bit):
                    top = bit
            result |= ((top + 974) << 52) | ((q << (52 - top)) & 0xfffffffffffff)
    return result, flags


class DivSqrtRecF64MulAddZ31:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_sqrtOp, in_a, in_b, in_roundingMode, in_detectTininess):
        sa = (in_a >> 64) & 1
        sb = (in_b >> 64) & 1
        ea = (in_a >> 52) & 4095
        eb = (in_b >> 52) & 4095
        fa = in_a & 0xfffffffffffff
        fb = in_b & 0xfffffffffffff
        ta = ea >> 9
        tb = eb >> 9
        nan_a = ta == 7
        nan_b = tb == 7
        snan_a = nan_a and (fa & (1 << 51)) == 0
        snan_b = nan_b and (fb & (1 << 51)) == 0
        mode = in_roundingMode & 7
        sign = sa ^ sb
        result = sign << 64
        flags = 0
        if (in_sqrtOp & 1) != 0:
            result = sb << 64
            if nan_b or (sb != 0 and tb != 0):
                result = 0xe008000000000000
                if snan_b or (not nan_b and sb != 0 and tb != 0):
                    flags = 16
            elif tb == 6:
                result = 0xc000000000000000
            elif tb != 0:
                e = eb - 2048
                parity = e & 1
                exponent = (e >> 1) + 2048
                radicand = ((1 << 52) | fb) << (56 + parity)
                root = 0
                # Restoring integer square root. No floating host primitive.
                for i in range(55):
                    bit = 54 - i
                    candidate = root | (1 << bit)
                    if candidate * candidate <= radicand:
                        root = candidate
                if root * root != radicand:
                    root |= 1
                result, flags = pack_rounded(root, exponent, mode, 0, in_detectTininess & 1)
        else:
            invalid = snan_a or snan_b or (ta == 0 and tb == 0) or (ta == 6 and tb == 6)
            if nan_a or nan_b or invalid:
                result = 0xe008000000000000
                if invalid:
                    flags = 16
            elif ta == 6 or tb == 0:
                result |= 0xc000000000000000
                if ta != 6 and tb == 0:
                    flags = 8
            elif ta != 0 and tb != 6:
                a = (1 << 52) | fa
                b = (1 << 52) | fb
                exponent = ea - eb + 2048
                shift = 54
                if a < b:
                    shift = 55
                    exponent -= 1
                numerator = a << shift
                q = numerator // b
                if numerator % b != 0:
                    q |= 1
                result, flags = pack_rounded(q, exponent, mode, sign, in_detectTininess & 1)
        return {"out_out": result, "out_exceptionFlags": flags}
