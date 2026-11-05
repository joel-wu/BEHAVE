def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class IntToRecFN:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_signedIn, in_in, in_roundingMode, in_detectTininess):
        x = to_unsigned(in_in, 32)
        if in_signedIn:
            int_value = x if x < (1 << 31) else x - (1 << 32)
        else:
            int_value = x

        if int_value < 0:
            sign = 1
            magnitude = -int_value
        else:
            sign = 0
            magnitude = int_value

        if magnitude == 0:
            return {"out_out": 0, "out_exceptionFlags": 0}

        p = 0
        for i in range(32):
            if (magnitude >> i) != 0:
                p = i

        if p <= 23:
            sig = magnitude << (23 - p)
            inexact = 0
            p_rounded = p
        else:
            shift = p - 23
            trunc = magnitude >> shift
            remainder = magnitude - (trunc << shift)
            half = 1 << (shift - 1)
            greater_than_half = remainder > half
            exactly_half = remainder == half
            inexact = 1 if remainder != 0 else 0

            rm = in_roundingMode & 0x7
            if rm == 5 or rm == 7:
                rm = 0

            if rm == 0:
                increment = 1 if (greater_than_half or (exactly_half and (trunc & 1))) else 0
            elif rm == 1:
                increment = 0
            elif rm == 2:
                increment = 1 if (sign and inexact) else 0
            elif rm == 3:
                increment = 1 if ((not sign) and inexact) else 0
            elif rm == 4:
                increment = 1 if (greater_than_half or exactly_half) else 0
            else:
                increment = 0

            sig_pre = trunc + increment
            if rm == 6 and inexact:
                sig_pre = trunc | 1
            if sig_pre == (1 << 24):
                sig = 1 << 23
                p_rounded = p + 1
            else:
                sig = sig_pre
                p_rounded = p

        exp_ieee = p_rounded + 127
        frac = sig - (1 << 23)
        rec_exp = exp_ieee + 129
        out_out = (sign << 32) | (rec_exp << 23) | frac
        out_exceptionFlags = inexact

        return {
            "out_out": to_unsigned(out_out, 33),
            "out_exceptionFlags": to_unsigned(out_exceptionFlags, 5),
        }
