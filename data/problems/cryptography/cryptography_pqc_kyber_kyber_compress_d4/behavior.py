def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class KyberPolyCompressD4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_coeff0,
        in_coeff1,
        in_coeff2,
        in_coeff3,
        in_coeff4,
        in_coeff5,
        in_coeff6,
        in_coeff7,
    ):
        if (
            not (-3329 < to_signed(in_coeff0, 16) < 3329)
            or not (-3329 < to_signed(in_coeff1, 16) < 3329)
            or not (-3329 < to_signed(in_coeff2, 16) < 3329)
            or not (-3329 < to_signed(in_coeff3, 16) < 3329)
            or not (-3329 < to_signed(in_coeff4, 16) < 3329)
            or not (-3329 < to_signed(in_coeff5, 16) < 3329)
            or not (-3329 < to_signed(in_coeff6, 16) < 3329)
            or not (-3329 < to_signed(in_coeff7, 16) < 3329)
        ):
            return {"out_byte0":0,"out_byte1":0,"out_byte2":0,"out_byte3":0,"out_error":1}
        coeffs = [
            to_signed(in_coeff0, 16),
            to_signed(in_coeff1, 16),
            to_signed(in_coeff2, 16),
            to_signed(in_coeff3, 16),
            to_signed(in_coeff4, 16),
            to_signed(in_coeff5, 16),
            to_signed(in_coeff6, 16),
            to_signed(in_coeff7, 16),
        ]

        t = [0] * 8
        for k in range(8):
            c = coeffs[k]
            u = c + 3329 if c < 0 else c
            d0 = u << 4
            d0 = d0 + 1665
            d0 = d0 * 80635
            d0 = d0 >> 28
            t[k] = d0 & 0xF

        return {"out_error": 0,
            "out_byte0": to_unsigned(t[0] | (t[1] << 4), 8),
            "out_byte1": to_unsigned(t[2] | (t[3] << 4), 8),
            "out_byte2": to_unsigned(t[4] | (t[5] << 4), 8),
            "out_byte3": to_unsigned(t[6] | (t[7] << 4), 8),
        }
