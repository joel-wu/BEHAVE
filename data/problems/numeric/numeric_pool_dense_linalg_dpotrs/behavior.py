def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def div0_s32(n, d):
    n = to_signed(n, 32)
    d = to_signed(d, 32)
    if d == 0:
        return 0
    q = (n if n >= 0 else -n) // (d if d >= 0 else -d)
    if (n < 0) ^ (d < 0):
        q = -q
    return to_signed(q, 32)


class CholeskySolve4x2:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_x00 = 0
        self.out_x01 = 0
        self.out_x10 = 0
        self.out_x11 = 0
        self.out_x20 = 0
        self.out_x21 = 0
        self.out_x30 = 0
        self.out_x31 = 0

    def process(
        self,
        in_uplo,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_b00, in_b01,
        in_b10, in_b11,
        in_b20, in_b21,
        in_b30, in_b31,
    ):
        t00 = to_signed(in_a00, 16)
        t01 = to_signed(in_a01, 16)
        t02 = to_signed(in_a02, 16)
        t03 = to_signed(in_a03, 16)
        t10 = to_signed(in_a10, 16)
        t11 = to_signed(in_a11, 16)
        t12 = to_signed(in_a12, 16)
        t13 = to_signed(in_a13, 16)
        t20 = to_signed(in_a20, 16)
        t21 = to_signed(in_a21, 16)
        t22 = to_signed(in_a22, 16)
        t23 = to_signed(in_a23, 16)
        t30 = to_signed(in_a30, 16)
        t31 = to_signed(in_a31, 16)
        t32 = to_signed(in_a32, 16)
        t33 = to_signed(in_a33, 16)

        b00 = to_signed(in_b00, 16)
        b01 = to_signed(in_b01, 16)
        b10 = to_signed(in_b10, 16)
        b11 = to_signed(in_b11, 16)
        b20 = to_signed(in_b20, 16)
        b21 = to_signed(in_b21, 16)
        b30 = to_signed(in_b30, 16)
        b31 = to_signed(in_b31, 16)

        if in_uplo:
            y00 = div0_s32(b00, t00)
            y10 = div0_s32(b10 - t01 * y00, t11)
            y20 = div0_s32(b20 - t02 * y00 - t12 * y10, t22)
            y30 = div0_s32(b30 - t03 * y00 - t13 * y10 - t23 * y20, t33)

            y01 = div0_s32(b01, t00)
            y11 = div0_s32(b11 - t01 * y01, t11)
            y21 = div0_s32(b21 - t02 * y01 - t12 * y11, t22)
            y31 = div0_s32(b31 - t03 * y01 - t13 * y11 - t23 * y21, t33)

            x30 = div0_s32(y30, t33)
            x20 = div0_s32(y20 - t23 * x30, t22)
            x10 = div0_s32(y10 - t12 * x20 - t13 * x30, t11)
            x00 = div0_s32(y00 - t01 * x10 - t02 * x20 - t03 * x30, t00)

            x31 = div0_s32(y31, t33)
            x21 = div0_s32(y21 - t23 * x31, t22)
            x11 = div0_s32(y11 - t12 * x21 - t13 * x31, t11)
            x01 = div0_s32(y01 - t01 * x11 - t02 * x21 - t03 * x31, t00)
        else:
            y00 = div0_s32(b00, t00)
            y10 = div0_s32(b10 - t10 * y00, t11)
            y20 = div0_s32(b20 - t20 * y00 - t21 * y10, t22)
            y30 = div0_s32(b30 - t30 * y00 - t31 * y10 - t32 * y20, t33)

            y01 = div0_s32(b01, t00)
            y11 = div0_s32(b11 - t10 * y01, t11)
            y21 = div0_s32(b21 - t20 * y01 - t21 * y11, t22)
            y31 = div0_s32(b31 - t30 * y01 - t31 * y11 - t32 * y21, t33)

            x30 = div0_s32(y30, t33)
            x20 = div0_s32(y20 - t32 * x30, t22)
            x10 = div0_s32(y10 - t21 * x20 - t31 * x30, t11)
            x00 = div0_s32(y00 - t10 * x10 - t20 * x20 - t30 * x30, t00)

            x31 = div0_s32(y31, t33)
            x21 = div0_s32(y21 - t32 * x31, t22)
            x11 = div0_s32(y11 - t21 * x21 - t31 * x31, t11)
            x01 = div0_s32(y01 - t10 * x11 - t20 * x21 - t30 * x31, t00)

        self.out_x00 = to_unsigned(x00, 16)
        self.out_x01 = to_unsigned(x01, 16)
        self.out_x10 = to_unsigned(x10, 16)
        self.out_x11 = to_unsigned(x11, 16)
        self.out_x20 = to_unsigned(x20, 16)
        self.out_x21 = to_unsigned(x21, 16)
        self.out_x30 = to_unsigned(x30, 16)
        self.out_x31 = to_unsigned(x31, 16)

        return {
            "out_x00": self.out_x00,
            "out_x01": self.out_x01,
            "out_x10": self.out_x10,
            "out_x11": self.out_x11,
            "out_x20": self.out_x20,
            "out_x21": self.out_x21,
            "out_x30": self.out_x30,
            "out_x31": self.out_x31,
        }