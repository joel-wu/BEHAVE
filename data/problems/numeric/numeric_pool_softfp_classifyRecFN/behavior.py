def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RecodedFp32Classifier:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_recfn):
        sign = (in_recfn >> 32) & 1
        exp_rec = (in_recfn >> 23) & 0x1FF
        fract = in_recfn & 0x7FFFFF

        exp_top3 = (exp_rec >> 6) & 0x7

        isZero = exp_top3 == 0
        isInf = exp_top3 == 6
        isNaN = exp_top3 == 7
        isFiniteNonzero = (not isZero) and (not isInf) and (not isNaN)

        isSigNaN = isNaN and (((fract >> 22) & 1) == 0)

        minNormExp = 130
        sExp = exp_rec
        isSubnormal = sExp < minNormExp

        out_class = (
            ((1 if (isNaN and (not isSigNaN)) else 0) << 9)
            | ((1 if isSigNaN else 0) << 8)
            | ((1 if ((not sign) and isInf) else 0) << 7)
            | ((1 if ((not sign) and isFiniteNonzero and (not isSubnormal)) else 0) << 6)
            | ((1 if ((not sign) and isFiniteNonzero and isSubnormal) else 0) << 5)
            | ((1 if ((not sign) and isZero) else 0) << 4)
            | ((1 if (sign and isZero) else 0) << 3)
            | ((1 if (sign and isFiniteNonzero and isSubnormal) else 0) << 2)
            | ((1 if (sign and isFiniteNonzero and (not isSubnormal)) else 0) << 1)
            | ((1 if (sign and isInf) else 0) << 0)
        )

        return {"out_class": to_unsigned(out_class, 10)}
