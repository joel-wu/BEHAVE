def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class NegacyclicShiftPolyCoeffMod:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_c0, in_c1, in_c2, in_c3, in_c4, in_c5, in_c6, in_c7,
        in_shift, in_modulus
    ):
        q = in_modulus
        s = in_shift
        coeffs = [in_c0, in_c1, in_c2, in_c3, in_c4, in_c5, in_c6, in_c7]
        result = [0] * 8
        for i in range(8):
            index_raw = i + s
            dst = index_raw & 7
            coeff = coeffs[i]
            if index_raw < 8 or coeff == 0:
                result[dst] = coeff
            else:
                result[dst] = q - coeff
        return {
            "out_c0": to_unsigned(result[0], 64),
            "out_c1": to_unsigned(result[1], 64),
            "out_c2": to_unsigned(result[2], 64),
            "out_c3": to_unsigned(result[3], 64),
            "out_c4": to_unsigned(result[4], 64),
            "out_c5": to_unsigned(result[5], 64),
            "out_c6": to_unsigned(result[6], 64),
            "out_c7": to_unsigned(result[7], 64),
        }