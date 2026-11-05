from fp_lib import fp32_cmp, fp32_div, fp32_mul, fp32_sqrt, fp32_sub


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Cholesky4x4Fp32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33
    ):
        zero = 0x00000000
        one = 0x3F800000

        g00 = in_a00
        g10 = in_a10
        g20 = in_a20
        g30 = in_a30

        if fp32_cmp(g00, zero) <= 0:
            return {
                "out_l00": 0,
                "out_l01": 0,
                "out_l02": 0,
                "out_l03": 0,
                "out_l10": 0,
                "out_l11": 0,
                "out_l12": 0,
                "out_l13": 0,
                "out_l20": 0,
                "out_l21": 0,
                "out_l22": 0,
                "out_l23": 0,
                "out_l30": 0,
                "out_l31": 0,
                "out_l32": 0,
                "out_l33": 0,
                "out_status": 1,
            }

        r0 = fp32_div(one, fp32_sqrt(g00))
        l00 = fp32_mul(g00, r0)
        l10 = fp32_mul(g10, r0)
        l20 = fp32_mul(g20, r0)
        l30 = fp32_mul(g30, r0)

        g11 = fp32_sub(in_a11, fp32_mul(l10, l10))
        g21 = fp32_sub(in_a21, fp32_mul(l10, l20))
        g31 = fp32_sub(in_a31, fp32_mul(l10, l30))

        if fp32_cmp(g11, zero) <= 0:
            return {
                "out_l00": 0,
                "out_l01": 0,
                "out_l02": 0,
                "out_l03": 0,
                "out_l10": 0,
                "out_l11": 0,
                "out_l12": 0,
                "out_l13": 0,
                "out_l20": 0,
                "out_l21": 0,
                "out_l22": 0,
                "out_l23": 0,
                "out_l30": 0,
                "out_l31": 0,
                "out_l32": 0,
                "out_l33": 0,
                "out_status": 1,
            }

        r1 = fp32_div(one, fp32_sqrt(g11))
        l11 = fp32_mul(g11, r1)
        l21 = fp32_mul(g21, r1)
        l31 = fp32_mul(g31, r1)

        t20 = fp32_sub(in_a22, fp32_mul(l20, l20))
        g22 = fp32_sub(t20, fp32_mul(l21, l21))
        t30 = fp32_sub(in_a32, fp32_mul(l20, l30))
        g32 = fp32_sub(t30, fp32_mul(l21, l31))

        if fp32_cmp(g22, zero) <= 0:
            return {
                "out_l00": 0,
                "out_l01": 0,
                "out_l02": 0,
                "out_l03": 0,
                "out_l10": 0,
                "out_l11": 0,
                "out_l12": 0,
                "out_l13": 0,
                "out_l20": 0,
                "out_l21": 0,
                "out_l22": 0,
                "out_l23": 0,
                "out_l30": 0,
                "out_l31": 0,
                "out_l32": 0,
                "out_l33": 0,
                "out_status": 1,
            }

        r2 = fp32_div(one, fp32_sqrt(g22))
        l22 = fp32_mul(g22, r2)
        l32 = fp32_mul(g32, r2)

        t31 = fp32_sub(in_a33, fp32_mul(l30, l30))
        t32 = fp32_sub(t31, fp32_mul(l31, l31))
        g33 = fp32_sub(t32, fp32_mul(l32, l32))

        if fp32_cmp(g33, zero) <= 0:
            return {
                "out_l00": 0,
                "out_l01": 0,
                "out_l02": 0,
                "out_l03": 0,
                "out_l10": 0,
                "out_l11": 0,
                "out_l12": 0,
                "out_l13": 0,
                "out_l20": 0,
                "out_l21": 0,
                "out_l22": 0,
                "out_l23": 0,
                "out_l30": 0,
                "out_l31": 0,
                "out_l32": 0,
                "out_l33": 0,
                "out_status": 1,
            }

        r3 = fp32_div(one, fp32_sqrt(g33))
        l33 = fp32_mul(g33, r3)

        return {
            "out_l00": to_unsigned(l00, 32),
            "out_l01": 0,
            "out_l02": 0,
            "out_l03": 0,
            "out_l10": to_unsigned(l10, 32),
            "out_l11": to_unsigned(l11, 32),
            "out_l12": 0,
            "out_l13": 0,
            "out_l20": to_unsigned(l20, 32),
            "out_l21": to_unsigned(l21, 32),
            "out_l22": to_unsigned(l22, 32),
            "out_l23": 0,
            "out_l30": to_unsigned(l30, 32),
            "out_l31": to_unsigned(l31, 32),
            "out_l32": to_unsigned(l32, 32),
            "out_l33": to_unsigned(l33, 32),
            "out_status": 0,
        }
