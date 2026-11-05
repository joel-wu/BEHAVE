def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def mul_q16(x, y):
    x = to_signed(x, 32)
    y = to_signed(y, 32)
    p = x * y
    if p >= 0:
        q = p >> 16
    else:
        q = -(((-p) >> 16))
    return to_signed(q, 32)


def div_q16(x, y):
    x = to_signed(x, 32)
    y = to_signed(y, 32)
    if y == 0:
        return 0
    n = x << 16
    an = n if n >= 0 else -n
    ay = y if y >= 0 else -y
    q = an // ay
    if (n >= 0) == (y >= 0):
        return to_signed(q, 32)
    return to_signed(-q, 32)


class TriangularSolve2x2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_uplo,
        in_transa,
        in_diag,
        in_alpha,
        in_a00,
        in_a01,
        in_a10,
        in_a11,
        in_b00,
        in_b01,
        in_b10,
        in_b11,
    ):
        alpha = to_signed(in_alpha, 32)
        a00 = to_signed(in_a00, 32)
        a01 = to_signed(in_a01, 32)
        a10 = to_signed(in_a10, 32)
        a11 = to_signed(in_a11, 32)
        b00 = to_signed(in_b00, 32)
        b01 = to_signed(in_b01, 32)
        b10 = to_signed(in_b10, 32)
        b11 = to_signed(in_b11, 32)

        if alpha == 0:
            b00 = 0
            b01 = 0
            b10 = 0
            b11 = 0
        elif in_side == 0:
            if in_transa == 0:
                if in_uplo == 0:
                    if alpha != 0x00010000:
                        b00 = mul_q16(alpha, b00)
                        b10 = mul_q16(alpha, b10)
                    if b10 != 0:
                        if in_diag == 0:
                            b10 = div_q16(b10, a11)
                        b00 = to_signed(b00 - mul_q16(b10, a01), 32)
                    if b00 != 0 and in_diag == 0:
                        b00 = div_q16(b00, a00)

                    if alpha != 0x00010000:
                        b01 = mul_q16(alpha, b01)
                        b11 = mul_q16(alpha, b11)
                    if b11 != 0:
                        if in_diag == 0:
                            b11 = div_q16(b11, a11)
                        b01 = to_signed(b01 - mul_q16(b11, a01), 32)
                    if b01 != 0 and in_diag == 0:
                        b01 = div_q16(b01, a00)
                else:
                    if alpha != 0x00010000:
                        b00 = mul_q16(alpha, b00)
                        b10 = mul_q16(alpha, b10)
                    if b00 != 0:
                        if in_diag == 0:
                            b00 = div_q16(b00, a00)
                        b10 = to_signed(b10 - mul_q16(b00, a10), 32)
                    if b10 != 0 and in_diag == 0:
                        b10 = div_q16(b10, a11)

                    if alpha != 0x00010000:
                        b01 = mul_q16(alpha, b01)
                        b11 = mul_q16(alpha, b11)
                    if b01 != 0:
                        if in_diag == 0:
                            b01 = div_q16(b01, a00)
                        b11 = to_signed(b11 - mul_q16(b01, a10), 32)
                    if b11 != 0 and in_diag == 0:
                        b11 = div_q16(b11, a11)
            else:
                if in_uplo == 0:
                    temp0 = mul_q16(alpha, b00)
                    if in_diag == 0:
                        temp0 = div_q16(temp0, a00)
                    b00 = temp0
                    temp1 = to_signed(mul_q16(alpha, b10) - mul_q16(a01, b00), 32)
                    if in_diag == 0:
                        temp1 = div_q16(temp1, a11)
                    b10 = temp1

                    temp0 = mul_q16(alpha, b01)
                    if in_diag == 0:
                        temp0 = div_q16(temp0, a00)
                    b01 = temp0
                    temp1 = to_signed(mul_q16(alpha, b11) - mul_q16(a01, b01), 32)
                    if in_diag == 0:
                        temp1 = div_q16(temp1, a11)
                    b11 = temp1
                else:
                    temp1 = mul_q16(alpha, b10)
                    if in_diag == 0:
                        temp1 = div_q16(temp1, a11)
                    b10 = temp1
                    temp0 = to_signed(mul_q16(alpha, b00) - mul_q16(a10, b10), 32)
                    if in_diag == 0:
                        temp0 = div_q16(temp0, a00)
                    b00 = temp0

                    temp1 = mul_q16(alpha, b11)
                    if in_diag == 0:
                        temp1 = div_q16(temp1, a11)
                    b11 = temp1
                    temp0 = to_signed(mul_q16(alpha, b01) - mul_q16(a10, b11), 32)
                    if in_diag == 0:
                        temp0 = div_q16(temp0, a00)
                    b01 = temp0
        else:
            if in_transa == 0:
                if in_uplo == 0:
                    if alpha != 0x00010000:
                        b00 = mul_q16(alpha, b00)
                        b10 = mul_q16(alpha, b10)
                    if in_diag == 0:
                        temp = div_q16(0x00010000, a00)
                        b00 = mul_q16(temp, b00)
                        b10 = mul_q16(temp, b10)

                    if alpha != 0x00010000:
                        b01 = mul_q16(alpha, b01)
                        b11 = mul_q16(alpha, b11)
                    if a01 != 0:
                        b01 = to_signed(b01 - mul_q16(a01, b00), 32)
                        b11 = to_signed(b11 - mul_q16(a01, b10), 32)
                    if in_diag == 0:
                        temp = div_q16(0x00010000, a11)
                        b01 = mul_q16(temp, b01)
                        b11 = mul_q16(temp, b11)
                else:
                    if alpha != 0x00010000:
                        b01 = mul_q16(alpha, b01)
                        b11 = mul_q16(alpha, b11)
                    if in_diag == 0:
                        temp = div_q16(0x00010000, a11)
                        b01 = mul_q16(temp, b01)
                        b11 = mul_q16(temp, b11)

                    if alpha != 0x00010000:
                        b00 = mul_q16(alpha, b00)
                        b10 = mul_q16(alpha, b10)
                    if a10 != 0:
                        b00 = to_signed(b00 - mul_q16(a10, b01), 32)
                        b10 = to_signed(b10 - mul_q16(a10, b11), 32)
                    if in_diag == 0:
                        temp = div_q16(0x00010000, a00)
                        b00 = mul_q16(temp, b00)
                        b10 = mul_q16(temp, b10)
            else:
                if in_uplo == 0:
                    if in_diag == 0:
                        temp = div_q16(0x00010000, a11)
                        b01 = mul_q16(temp, b01)
                        b11 = mul_q16(temp, b11)
                    if a01 != 0:
                        b00 = to_signed(b00 - mul_q16(a01, b01), 32)
                        b10 = to_signed(b10 - mul_q16(a01, b11), 32)
                    if alpha != 0x00010000:
                        b01 = mul_q16(alpha, b01)
                        b11 = mul_q16(alpha, b11)

                    if in_diag == 0:
                        temp = div_q16(0x00010000, a00)
                        b00 = mul_q16(temp, b00)
                        b10 = mul_q16(temp, b10)
                    if alpha != 0x00010000:
                        b00 = mul_q16(alpha, b00)
                        b10 = mul_q16(alpha, b10)
                else:
                    if in_diag == 0:
                        temp = div_q16(0x00010000, a00)
                        b00 = mul_q16(temp, b00)
                        b10 = mul_q16(temp, b10)
                    if a10 != 0:
                        b01 = to_signed(b01 - mul_q16(a10, b00), 32)
                        b11 = to_signed(b11 - mul_q16(a10, b10), 32)
                    if alpha != 0x00010000:
                        b00 = mul_q16(alpha, b00)
                        b10 = mul_q16(alpha, b10)

                    if in_diag == 0:
                        temp = div_q16(0x00010000, a11)
                        b01 = mul_q16(temp, b01)
                        b11 = mul_q16(temp, b11)
                    if alpha != 0x00010000:
                        b01 = mul_q16(alpha, b01)
                        b11 = mul_q16(alpha, b11)

        return {
            "out_b00": to_unsigned(b00, 32),
            "out_b01": to_unsigned(b01, 32),
            "out_b10": to_unsigned(b10, 32),
            "out_b11": to_unsigned(b11, 32),
        }