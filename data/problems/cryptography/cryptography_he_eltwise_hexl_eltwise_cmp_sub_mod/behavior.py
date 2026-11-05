def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyHeEltwiseHexlEltwiseCmpSubMod:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_operand1, in_modulus, in_cmp, in_bound, in_diff):
        x = to_unsigned(in_operand1, 64)
        m = to_unsigned(in_modulus, 64)
        c = to_unsigned(in_cmp, 3)
        b = to_unsigned(in_bound, 64)
        d = to_unsigned(in_diff, 64)

        if c == 0:
            p = x == b
        elif c == 1:
            p = x != b
        elif c == 2:
            p = x < b
        elif c == 3:
            p = x <= b
        elif c == 4:
            p = x > b
        elif c == 5:
            p = x >= b
        else:
            p = False

        if m == 0:
            r = 0
        elif not p:
            r = x % m
        elif x >= d:
            r = (x - d) % m
        else:
            r = (m - ((d - x) % m)) % m

        return {"out_result": to_unsigned(r, 64)}