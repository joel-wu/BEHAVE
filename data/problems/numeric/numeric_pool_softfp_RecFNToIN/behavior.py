def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RecfnToIntI32:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_out = 0
        self.out_intExceptionFlags = 0

    def process(self, in_in, in_roundingMode, in_signedOut):
        sign = (in_in >> 32) & 1
        expRec = (in_in >> 23) & 0x1FF
        fract = in_in & 0x7FFFFF
        expTop = (expRec >> 6) & 0x7

        isNaN = expTop == 7
        isInf = expTop == 6
        isZero = expTop == 0
        sExp_tc = expRec
        magGeOne = (sExp_tc >> 8) & 1
        posExp = sExp_tc & 0xFF
        magJustBelowOne = 1 if ((not magGeOne) and posExp == 0xFF) else 0

        shiftedSigBase = (magGeOne << 23) | fract
        shiftAmt = (sExp_tc & 0x1F) if magGeOne else 0
        shiftedSig = shiftedSigBase << shiftAmt
        sticky = 1 if (shiftedSig & ((1 << 22) - 1)) != 0 else 0
        alignedSig = ((shiftedSig >> 22) << 1) | sticky
        unroundedInt = to_unsigned(alignedSig >> 2, 32)

        common_inexact = 1 if ((magGeOne and ((alignedSig & 0x3) != 0)) or ((not magGeOne) and (not isZero))) else 0
        roundIncr_near_even = 1 if ((magGeOne and ((((alignedSig >> 1) & 0x3) == 0x3) or ((alignedSig & 0x3) == 0x3))) or (magJustBelowOne and ((alignedSig & 0x3) != 0))) else 0
        roundIncr_near_maxMag = 1 if ((magGeOne and ((alignedSig >> 1) & 1)) or magJustBelowOne) else 0

        roundingMode_near_even = 1 if in_roundingMode == 0 else 0
        roundingMode_minMag = 1 if in_roundingMode == 1 else 0
        roundingMode_min = 1 if in_roundingMode == 2 else 0
        roundingMode_max = 1 if in_roundingMode == 3 else 0
        roundingMode_near_maxMag = 1 if in_roundingMode == 4 else 0
        roundingMode_odd = 1 if in_roundingMode == 6 else 0

        roundIncr = 1 if (
            (roundingMode_near_even and roundIncr_near_even)
            or (roundingMode_near_maxMag and roundIncr_near_maxMag)
            or ((roundingMode_min or roundingMode_odd) and sign and common_inexact)
            or (roundingMode_max and (not sign) and common_inexact)
        ) else 0

        complUnroundedInt = to_unsigned((~unroundedInt) if sign else unroundedInt, 32)
        roundedCore = to_unsigned((complUnroundedInt + 1) if (roundIncr ^ sign) else complUnroundedInt, 32)
        roundedInt = to_unsigned(roundedCore | (1 if (roundingMode_odd and common_inexact) else 0), 32)

        magGeOne_atOverflowEdge = 1 if posExp == 31 else 0
        roundCarryBut2 = 1 if ((unroundedInt & 0x3FFFFFFF) == 0x3FFFFFFF and roundIncr) else 0

        if magGeOne:
            if posExp >= 32:
                common_overflow = 1
            elif in_signedOut:
                if sign:
                    common_overflow = 1 if (magGeOne_atOverflowEdge and (((unroundedInt & 0x7FFFFFFF) != 0) or roundIncr)) else 0
                else:
                    common_overflow = 1 if (magGeOne_atOverflowEdge or ((posExp == 30) and roundCarryBut2)) else 0
            else:
                common_overflow = 1 if (sign or (magGeOne_atOverflowEdge and ((unroundedInt >> 30) & 1) and roundCarryBut2)) else 0
        else:
            common_overflow = 1 if ((not in_signedOut) and sign and roundIncr) else 0

        invalidExc = 1 if (isNaN or isInf) else 0
        overflow = 1 if ((not invalidExc) and common_overflow) else 0
        inexact = 1 if ((not invalidExc) and (not common_overflow) and common_inexact) else 0

        excSign = 1 if ((not isNaN) and sign) else 0
        excOut = (((1 << 31) if (in_signedOut == excSign) else 0) |
                  (((1 << 31) - 1) if (not excSign) else 0))

        out_out = excOut if (invalidExc or common_overflow) else roundedInt
        out_intExceptionFlags = ((invalidExc << 2) | (overflow << 1) | inexact) & 0x7

        self.out_out = to_unsigned(out_out, 32)
        self.out_intExceptionFlags = out_intExceptionFlags
        return {
            "out_out": self.out_out,
            "out_intExceptionFlags": self.out_intExceptionFlags,
        }
