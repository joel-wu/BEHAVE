def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class KyberPolyToBytesPair:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_coeff0, in_coeff1):
        if (
            not (-3329 < to_signed(in_coeff0, 16) < 3329)
            or not (-3329 < to_signed(in_coeff1, 16) < 3329)
        ):
            return {"out_byte0":0,"out_byte1":0,"out_byte2":0,"out_error":1}
        c0 = to_signed(in_coeff0, 16)
        c1 = to_signed(in_coeff1, 16)
        t0 = c0 + ((c0 >> 15) & 3329)
        t1 = c1 + ((c1 >> 15) & 3329)
        out_byte0 = t0 & 0xFF
        out_byte1 = ((t0 >> 8) | (t1 << 4)) & 0xFF
        out_byte2 = (t1 >> 4) & 0xFF
        return {"out_error": 0,
            "out_byte0": to_unsigned(out_byte0, 8),
            "out_byte1": to_unsigned(out_byte1, 8),
            "out_byte2": to_unsigned(out_byte2, 8),
        }
