def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    return v - (1 << b) if v & (1 << (b - 1)) else v


class WinogradF23InputXform:
    def __init__(self):
        self.reset()

    def reset(self):
        self._dummy = 0

    def process(
        self,
        in_d00, in_d01, in_d02, in_d03,
        in_d10, in_d11, in_d12, in_d13,
        in_d20, in_d21, in_d22, in_d23,
        in_d30, in_d31, in_d32, in_d33,
    ):
        d00 = to_signed(in_d00, 16)
        d01 = to_signed(in_d01, 16)
        d02 = to_signed(in_d02, 16)
        d03 = to_signed(in_d03, 16)
        d10 = to_signed(in_d10, 16)
        d11 = to_signed(in_d11, 16)
        d12 = to_signed(in_d12, 16)
        d13 = to_signed(in_d13, 16)
        d20 = to_signed(in_d20, 16)
        d21 = to_signed(in_d21, 16)
        d22 = to_signed(in_d22, 16)
        d23 = to_signed(in_d23, 16)
        d30 = to_signed(in_d30, 16)
        d31 = to_signed(in_d31, 16)
        d32 = to_signed(in_d32, 16)
        d33 = to_signed(in_d33, 16)

        m00 = d00 - d20
        m01 = d01 - d21
        m02 = d02 - d22
        m03 = d03 - d23

        m10 = d10 + d20
        m11 = d11 + d21
        m12 = d12 + d22
        m13 = d13 + d23

        m20 = d20 - d10
        m21 = d21 - d11
        m22 = d22 - d12
        m23 = d23 - d13

        m30 = d10 - d30
        m31 = d11 - d31
        m32 = d12 - d32
        m33 = d13 - d33

        t00 = m00 - m02
        t01 = m01 + m02
        t02 = m02 - m01
        t03 = m01 - m03

        t10 = m10 - m12
        t11 = m11 + m12
        t12 = m12 - m11
        t13 = m11 - m13

        t20 = m20 - m22
        t21 = m21 + m22
        t22 = m22 - m21
        t23 = m21 - m23

        t30 = m30 - m32
        t31 = m31 + m32
        t32 = m32 - m31
        t33 = m31 - m33

        return {
            "out_t00": to_unsigned(t00, 20),
            "out_t01": to_unsigned(t01, 20),
            "out_t02": to_unsigned(t02, 20),
            "out_t03": to_unsigned(t03, 20),
            "out_t10": to_unsigned(t10, 20),
            "out_t11": to_unsigned(t11, 20),
            "out_t12": to_unsigned(t12, 20),
            "out_t13": to_unsigned(t13, 20),
            "out_t20": to_unsigned(t20, 20),
            "out_t21": to_unsigned(t21, 20),
            "out_t22": to_unsigned(t22, 20),
            "out_t23": to_unsigned(t23, 20),
            "out_t30": to_unsigned(t30, 20),
            "out_t31": to_unsigned(t31, 20),
            "out_t32": to_unsigned(t32, 20),
            "out_t33": to_unsigned(t33, 20),
        }
