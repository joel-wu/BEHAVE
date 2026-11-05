def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RecFNFromFN32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_fn):
        in_fn = to_unsigned(in_fn, 32)
        sign = (in_fn >> 31) & 1
        exp_in = (in_fn >> 23) & 0xFF
        fract_in = in_fn & 0x7FFFFF

        isZero = exp_in == 0 and fract_in == 0
        isNaN = exp_in == 0xFF and fract_in != 0
        isSubnormal = exp_in == 0 and fract_in != 0
        isNormal = exp_in != 0 and exp_in != 0xFF

        if isZero:
            sig = 0
            sExp = 0
        elif isNormal:
            sig = (1 << 23) | fract_in
            sExp = exp_in + 129
        elif isSubnormal:
            k = 0
            for i in range(22, -1, -1):
                if ((fract_in >> i) & 1) != 0:
                    k = 22 - i
                    break
            sig = to_unsigned(fract_in << (k + 1), 24)
            sExp = 129 - k
        else:
            sig = (1 << 23) | fract_in
            sExp = 0b110000000

        if isZero:
            recExp = 0
        else:
            upper = ((sExp >> 6) & 0x7) | (1 if isNaN else 0)
            lower = sExp & 0x3F
            recExp = (upper << 6) | lower

        recSig = sig & 0x7FFFFF
        out_recfn = (sign << 32) | (recExp << 23) | recSig
        return {"out_recfn": to_unsigned(out_recfn, 33)}
