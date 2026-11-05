def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyPkClassicMontInvModStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_start, in_a, in_modulus, in_accum, in_bit_index):
        modulus = to_unsigned(in_modulus, 64)
        a = to_unsigned(in_a, 64)
        accum = to_unsigned(in_accum, 64)
        bit_index = to_unsigned(in_bit_index, 7)
        exponent = to_unsigned(modulus - 2, 64)
        effective_accum = ((1 << 64) % modulus if modulus else 0) if in_start else accum
        exp_bit = ((exponent >> bit_index) & 1) if bit_index <= 63 else 0
        square = self.montgomery_mul(effective_accum, effective_accum, modulus)
        next_accum = self.montgomery_mul(square, a, modulus) if exp_bit else square
        return {
            "out_accum": to_unsigned(next_accum, 64),
            "out_bit_index": to_unsigned(bit_index - 1, 7),
            "out_done": 1 if bit_index == 0 else 0,
        }

    def inverse64(self, a):
        a = to_unsigned(a, 64)
        x = to_unsigned((((((a << 1) ^ a) & 4) << 1) ^ a), 64)
        x = to_unsigned(x + x - a * x * x, 64)
        x = to_unsigned(x + x - a * x * x, 64)
        x = to_unsigned(x + x - a * x * x, 64)
        x = to_unsigned(x + x - a * x * x, 64)
        return to_unsigned(x, 64)

    def montgomery_mul(self, x, y, modulus):
        x = to_unsigned(x, 64)
        y = to_unsigned(y, 64)
        modulus = to_unsigned(modulus, 64)
        m0 = to_unsigned(-self.inverse64(modulus), 64) if (modulus & 1) else 0
        t = x * y
        low_t = to_unsigned(t, 64)
        m = to_unsigned(low_t * m0, 64)
        u = (t + m * modulus) >> 64
        if u >= modulus:
            u -= modulus
        return to_unsigned(u, 64)
