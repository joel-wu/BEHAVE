def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SymmetricRankK4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_trans,
        in_alpha,
        in_beta,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_c00, in_c01, in_c02, in_c03,
        in_c10, in_c11, in_c12, in_c13,
        in_c20, in_c21, in_c22, in_c23,
        in_c30, in_c31, in_c32, in_c33
    ):
        alpha = to_signed(in_alpha, 32)
        beta = to_signed(in_beta, 32)

        A = [
            [to_signed(in_a00, 32), to_signed(in_a01, 32), to_signed(in_a02, 32), to_signed(in_a03, 32)],
            [to_signed(in_a10, 32), to_signed(in_a11, 32), to_signed(in_a12, 32), to_signed(in_a13, 32)],
            [to_signed(in_a20, 32), to_signed(in_a21, 32), to_signed(in_a22, 32), to_signed(in_a23, 32)],
            [to_signed(in_a30, 32), to_signed(in_a31, 32), to_signed(in_a32, 32), to_signed(in_a33, 32)],
        ]
        C_in = [
            [to_signed(in_c00, 32), to_signed(in_c01, 32), to_signed(in_c02, 32), to_signed(in_c03, 32)],
            [to_signed(in_c10, 32), to_signed(in_c11, 32), to_signed(in_c12, 32), to_signed(in_c13, 32)],
            [to_signed(in_c20, 32), to_signed(in_c21, 32), to_signed(in_c22, 32), to_signed(in_c23, 32)],
            [to_signed(in_c30, 32), to_signed(in_c31, 32), to_signed(in_c32, 32), to_signed(in_c33, 32)],
        ]

        C_out = [[C_in[r][c] for c in range(4)] for r in range(4)]

        for i in range(4):
            for j in range(4):
                selected = (i <= j) if in_uplo else (i >= j)
                if not selected:
                    continue
                if alpha == 0:
                    value = 0 if beta == 0 else beta * C_in[i][j]
                elif in_trans == 0:
                    temp0 = A[i][0] * A[j][0]
                    temp1 = temp0 + A[i][1] * A[j][1]
                    temp2 = temp1 + A[i][2] * A[j][2]
                    temp3 = temp2 + A[i][3] * A[j][3]
                    value = alpha * temp3 + beta * C_in[i][j]
                else:
                    temp0 = A[0][i] * A[0][j]
                    temp1 = temp0 + A[1][i] * A[1][j]
                    temp2 = temp1 + A[2][i] * A[2][j]
                    temp3 = temp2 + A[3][i] * A[3][j]
                    value = alpha * temp3 + beta * C_in[i][j]
                C_out[i][j] = to_signed(value, 32)

        return {
            "out_c00": to_unsigned(C_out[0][0], 32),
            "out_c01": to_unsigned(C_out[0][1], 32),
            "out_c02": to_unsigned(C_out[0][2], 32),
            "out_c03": to_unsigned(C_out[0][3], 32),
            "out_c10": to_unsigned(C_out[1][0], 32),
            "out_c11": to_unsigned(C_out[1][1], 32),
            "out_c12": to_unsigned(C_out[1][2], 32),
            "out_c13": to_unsigned(C_out[1][3], 32),
            "out_c20": to_unsigned(C_out[2][0], 32),
            "out_c21": to_unsigned(C_out[2][1], 32),
            "out_c22": to_unsigned(C_out[2][2], 32),
            "out_c23": to_unsigned(C_out[2][3], 32),
            "out_c30": to_unsigned(C_out[3][0], 32),
            "out_c31": to_unsigned(C_out[3][1], 32),
            "out_c32": to_unsigned(C_out[3][2], 32),
            "out_c33": to_unsigned(C_out[3][3], 32),
        }