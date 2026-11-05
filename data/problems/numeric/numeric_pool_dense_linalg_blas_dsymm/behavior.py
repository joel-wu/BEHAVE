def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SymmetricMatrixMultiply2x2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_uplo,
        in_alpha,
        in_beta,
        in_a00,
        in_a01,
        in_a10,
        in_a11,
        in_b00,
        in_b10,
        in_b01,
        in_b11,
        in_c00,
        in_c10,
        in_c01,
        in_c11,
    ):
        s = in_a01 if in_uplo == 0 else in_a10

        if in_side == 0:
            p00 = to_unsigned(to_unsigned(in_a00 * in_b00, 64) + to_unsigned(s * in_b10, 64), 64)
            p01 = to_unsigned(to_unsigned(in_a00 * in_b01, 64) + to_unsigned(s * in_b11, 64), 64)
            p10 = to_unsigned(to_unsigned(s * in_b00, 64) + to_unsigned(in_a11 * in_b10, 64), 64)
            p11 = to_unsigned(to_unsigned(s * in_b01, 64) + to_unsigned(in_a11 * in_b11, 64), 64)
        else:
            p00 = to_unsigned(to_unsigned(in_b00 * in_a00, 64) + to_unsigned(in_b01 * s, 64), 64)
            p01 = to_unsigned(to_unsigned(in_b00 * s, 64) + to_unsigned(in_b01 * in_a11, 64), 64)
            p10 = to_unsigned(to_unsigned(in_b10 * in_a00, 64) + to_unsigned(in_b11 * s, 64), 64)
            p11 = to_unsigned(to_unsigned(in_b10 * s, 64) + to_unsigned(in_b11 * in_a11, 64), 64)

        r00 = to_unsigned(to_unsigned(in_alpha * p00, 64) + to_unsigned(in_beta * in_c00, 64), 64)
        r01 = to_unsigned(to_unsigned(in_alpha * p01, 64) + to_unsigned(in_beta * in_c01, 64), 64)
        r10 = to_unsigned(to_unsigned(in_alpha * p10, 64) + to_unsigned(in_beta * in_c10, 64), 64)
        r11 = to_unsigned(to_unsigned(in_alpha * p11, 64) + to_unsigned(in_beta * in_c11, 64), 64)

        return {
            "out_c00": to_unsigned(r00, 64),
            "out_c10": to_unsigned(r10, 64),
            "out_c01": to_unsigned(r01, 64),
            "out_c11": to_unsigned(r11, 64),
        }