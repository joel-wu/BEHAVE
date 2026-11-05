def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CryptographyHeSealSealGaloisIndex:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_coeff_count_power, in_step):
        coeff_count_power = to_unsigned(in_coeff_count_power, 5)
        step = to_signed(in_step, 32)

        if step < 0:
            if step == -(1 << 31):
                pos_step = 1 << 31
            else:
                pos_step = -step
        else:
            pos_step = step

        if coeff_count_power < 1:
            return {"out_galois_elt": 0, "out_valid_step": 0}

        n = 1 << coeff_count_power
        half_n = n >> 1

        if pos_step >= half_n:
            return {"out_galois_elt": 0, "out_valid_step": 0}

        m = n << 1
        mask = m - 1

        if step == 0:
            return {
                "out_galois_elt": to_unsigned(m - 1, 32),
                "out_valid_step": 1,
            }

        if step < 0:
            exponent = half_n - pos_step
        else:
            exponent = pos_step

        galois_elt = 1
        power = 3 & mask
        for i in range(32):
            bit_index = i
            if (exponent >> bit_index) & 1:
                galois_elt = (galois_elt * power) & mask
            power = (power * power) & mask

        return {
            "out_galois_elt": to_unsigned(galois_elt, 32),
            "out_valid_step": 1,
        }
