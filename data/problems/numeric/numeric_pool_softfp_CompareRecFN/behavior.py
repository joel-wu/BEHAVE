def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class RecodedFpCompare:
    def __init__(self, exp_width=8, sig_width=24):
        self.exp_width = exp_width
        self.sig_width = sig_width
        self.reset()

    def reset(self):
        pass

    def _raw_float_from_recfn(self, x):
        total = self.exp_width + self.sig_width + 1
        x = to_unsigned(x, total)
        sign = (x >> (self.exp_width + self.sig_width)) & 1
        exp_rec = (x >> (self.sig_width - 1)) & ((1 << (self.exp_width + 1)) - 1)
        fract = x & ((1 << (self.sig_width - 1)) - 1)
        exp_code = (exp_rec >> (self.exp_width - 2)) & 0x7
        is_zero = 1 if exp_code == 0 else 0
        is_inf = 1 if exp_code == 0x6 else 0
        is_nan = 1 if exp_code == 0x7 else 0
        s_exp = exp_rec
        sig = fract
        return {
            "sign": sign,
            "isNaN": is_nan,
            "isInf": is_inf,
            "isZero": is_zero,
            "sExp": s_exp,
            "sig": sig,
            "fract": fract,
        }

    def _is_sig_nan_raw_float(self, raw):
        return 1 if raw["isNaN"] and (((raw["fract"] >> (self.sig_width - 2)) & 1) == 0) else 0

    def process(self, in_a, in_b, in_signaling):
        rawA = self._raw_float_from_recfn(in_a)
        rawB = self._raw_float_from_recfn(in_b)

        ordered = (not rawA["isNaN"]) and (not rawB["isNaN"])
        bothInfs = rawA["isInf"] and rawB["isInf"]
        bothZeros = rawA["isZero"] and rawB["isZero"]
        eqExps = rawA["sExp"] == rawB["sExp"]
        common_ltMags = (rawA["sExp"] < rawB["sExp"]) or (eqExps and (rawA["sig"] < rawB["sig"]))
        common_eqMags = eqExps and (rawA["sig"] == rawB["sig"])

        ordered_lt = (not bothZeros) and (
            (rawA["sign"] and (not rawB["sign"])) or
            ((not bothInfs) and (
                (rawA["sign"] and (not common_ltMags) and (not common_eqMags)) or
                ((not rawB["sign"]) and common_ltMags)
            ))
        )
        ordered_eq = bothZeros or (
            (rawA["sign"] == rawB["sign"]) and (bothInfs or common_eqMags)
        )

        invalid = self._is_sig_nan_raw_float(rawA) or self._is_sig_nan_raw_float(rawB) or (in_signaling and (not ordered))

        out_lt = 1 if (ordered and ordered_lt) else 0
        out_eq = 1 if (ordered and ordered_eq) else 0
        out_gt = 1 if (ordered and (not ordered_lt) and (not ordered_eq)) else 0
        out_exceptionFlags = 0x10 if invalid else 0x00

        return {
            "out_lt": to_unsigned(out_lt, 1),
            "out_eq": to_unsigned(out_eq, 1),
            "out_gt": to_unsigned(out_gt, 1),
            "out_exceptionFlags": to_unsigned(out_exceptionFlags, 5),
        }
