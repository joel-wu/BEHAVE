def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap32(v):
    return to_signed(v, 32)


def mul_q31(a, b):
    return wrap32((to_signed(a, 32) * to_signed(b, 32)) >> 31)


class ControlKalmanEkfPredictStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x0, in_x1,
        in_p00, in_p01, in_p10, in_p11,
        in_f00, in_f01, in_f10, in_f11,
        in_b0, in_b1, in_u,
        in_q00, in_q01, in_q10, in_q11
    ):
        x0 = to_signed(in_x0, 32)
        x1 = to_signed(in_x1, 32)
        p00 = to_signed(in_p00, 32)
        p01 = to_signed(in_p01, 32)
        p10 = to_signed(in_p10, 32)
        p11 = to_signed(in_p11, 32)
        f00 = to_signed(in_f00, 32)
        f01 = to_signed(in_f01, 32)
        f10 = to_signed(in_f10, 32)
        f11 = to_signed(in_f11, 32)
        b0 = to_signed(in_b0, 32)
        b1 = to_signed(in_b1, 32)
        u = to_signed(in_u, 32)
        q00 = to_signed(in_q00, 32)
        q01 = to_signed(in_q01, 32)
        q10 = to_signed(in_q10, 32)
        q11 = to_signed(in_q11, 32)

        out_x0 = wrap32(mul_q31(f00, x0) + mul_q31(f01, x1) + mul_q31(b0, u))
        out_x1 = wrap32(mul_q31(f10, x0) + mul_q31(f11, x1) + mul_q31(b1, u))

        a00 = wrap32(mul_q31(f00, p00) + mul_q31(f01, p10))
        a01 = wrap32(mul_q31(f00, p01) + mul_q31(f01, p11))
        a10 = wrap32(mul_q31(f10, p00) + mul_q31(f11, p10))
        a11 = wrap32(mul_q31(f10, p01) + mul_q31(f11, p11))

        ptmp00 = wrap32(mul_q31(a00, f00) + mul_q31(a01, f01))
        ptmp01 = wrap32(mul_q31(a00, f10) + mul_q31(a01, f11))
        ptmp10 = wrap32(mul_q31(a10, f00) + mul_q31(a11, f01))
        ptmp11 = wrap32(mul_q31(a10, f10) + mul_q31(a11, f11))

        out_p00 = wrap32(ptmp00 + q00)
        out_p01 = wrap32(ptmp01 + q01)
        out_p10 = wrap32(ptmp10 + q10)
        out_p11 = wrap32(ptmp11 + q11)

        return {
            "out_x0": to_unsigned(out_x0, 32),
            "out_x1": to_unsigned(out_x1, 32),
            "out_p00": to_unsigned(out_p00, 32),
            "out_p01": to_unsigned(out_p01, 32),
            "out_p10": to_unsigned(out_p10, 32),
            "out_p11": to_unsigned(out_p11, 32),
        }