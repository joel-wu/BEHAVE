MASK64 = (1 << 64) - 1


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DyadicModularMultiply:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_operand1, in_operand2, in_modulus, in_const_ratio_0, in_const_ratio_1):
        a = to_unsigned(in_operand1, 64)
        b = to_unsigned(in_operand2, 64)
        p = to_unsigned(in_modulus, 64)
        r0 = to_unsigned(in_const_ratio_0, 64)
        r1 = to_unsigned(in_const_ratio_1, 64)

        z = a * b
        z0 = z & MASK64
        z1 = (z >> 64) & MASK64

        carry0 = ((z0 * r0) >> 64) & MASK64

        prod01 = z0 * r1
        prod01_lo = prod01 & MASK64
        prod01_hi = (prod01 >> 64) & MASK64
        sum1 = prod01_lo + carry0
        sum1_lo = sum1 & MASK64
        sum1_carry = (sum1 >> 64) & MASK64
        tmp3 = (prod01_hi + sum1_carry) & MASK64

        prod10 = z1 * r0
        prod10_lo = prod10 & MASK64
        prod10_hi = (prod10 >> 64) & MASK64
        sum2 = sum1_lo + prod10_lo
        sum2_lo = sum2 & MASK64
        sum2_carry = (sum2 >> 64) & MASK64
        carry1 = (prod10_hi + sum2_carry) & MASK64

        tmp1 = (z1 * r1 + tmp3 + carry1) & MASK64
        t = (z0 - ((tmp1 * p) & MASK64)) & MASK64
        c = (t - p) & MASK64 if t >= p else t

        return {"out_result": to_unsigned(c, 64)}