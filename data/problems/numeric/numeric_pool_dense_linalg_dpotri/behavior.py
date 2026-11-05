def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero(n, d):
    q = abs(n) // abs(d)
    return -q if (n < 0) ^ (d < 0) else q


def mul_q(a, b):
    return trunc_div_toward_zero(a * b, 1 << 16)


def div_q(a, b):
    return trunc_div_toward_zero(a << 16, b)


class CholeskyInverse3x3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_a00, in_a01, in_a02,
        in_a10, in_a11, in_a12,
        in_a20, in_a21, in_a22,
    ):
        if in_uplo:
            u00 = to_signed(in_a00, 32)
            u01 = to_signed(in_a01, 32)
            u02 = to_signed(in_a02, 32)
            u11 = to_signed(in_a11, 32)
            u12 = to_signed(in_a12, 32)
            u22 = to_signed(in_a22, 32)
            if u00 == 0:
                info = 1
            elif u11 == 0:
                info = 2
            elif u22 == 0:
                info = 3
            else:
                info = 0
            if info != 0:
                c00 = c01 = c02 = c11 = c12 = c22 = 0
            else:
                r22 = div_q(1 << 16, u22)
                r11 = div_q(1 << 16, u11)
                r00 = div_q(1 << 16, u00)
                r12 = -div_q(mul_q(u12, r22), u11)
                r01 = -div_q(mul_q(u01, r11), u00)
                t0 = mul_q(u01, r12)
                t1 = mul_q(u02, r22)
                r02 = -div_q(t0 + t1, u00)
                c00 = mul_q(r00, r00) + mul_q(r01, r01) + mul_q(r02, r02)
                c01 = mul_q(r01, r11) + mul_q(r02, r12)
                c02 = mul_q(r02, r22)
                c11 = mul_q(r11, r11) + mul_q(r12, r12)
                c12 = mul_q(r12, r22)
                c22 = mul_q(r22, r22)
        else:
            l00 = to_signed(in_a00, 32)
            l10 = to_signed(in_a10, 32)
            l11 = to_signed(in_a11, 32)
            l20 = to_signed(in_a20, 32)
            l21 = to_signed(in_a21, 32)
            l22 = to_signed(in_a22, 32)
            if l00 == 0:
                info = 1
            elif l11 == 0:
                info = 2
            elif l22 == 0:
                info = 3
            else:
                info = 0
            if info != 0:
                c00 = c01 = c02 = c11 = c12 = c22 = 0
            else:
                r22 = div_q(1 << 16, l22)
                r11 = div_q(1 << 16, l11)
                r00 = div_q(1 << 16, l00)
                r21 = -div_q(mul_q(l21, r22), l11)
                r10 = -div_q(mul_q(l10, r11), l00)
                t0 = mul_q(l21, r10)
                t1 = mul_q(l20, r00)
                r20 = -div_q(t0 + t1, l22)
                c00 = mul_q(r00, r00) + mul_q(r10, r10) + mul_q(r20, r20)
                c01 = mul_q(r10, r11) + mul_q(r20, r21)
                c02 = mul_q(r20, r22)
                c11 = mul_q(r11, r11) + mul_q(r21, r21)
                c12 = mul_q(r21, r22)
                c22 = mul_q(r22, r22)
        return {
            "out_info": to_unsigned(info, 32),
            "out_a00": to_unsigned(c00, 32),
            "out_a01": to_unsigned(c01, 32),
            "out_a02": to_unsigned(c02, 32),
            "out_a10": to_unsigned(c01, 32),
            "out_a11": to_unsigned(c11, 32),
            "out_a12": to_unsigned(c12, 32),
            "out_a20": to_unsigned(c02, 32),
            "out_a21": to_unsigned(c12, 32),
            "out_a22": to_unsigned(c22, 32),
        }