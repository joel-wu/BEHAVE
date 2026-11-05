def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap64_signed(v):
    return to_signed(v, 64)


class TriangularBandMatrixVectorStep:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_x0 = 0
        self.out_x1 = 0
        self.out_x2 = 0
        self.out_x3 = 0

    def process(
        self,
        in_uplo,
        in_trans,
        in_diag,
        in_a00,
        in_a01,
        in_a10,
        in_a11,
        in_a20,
        in_a21,
        in_a30,
        in_a31,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
    ):
        a = [
            [to_signed(in_a00, 64), to_signed(in_a10, 64), to_signed(in_a20, 64), to_signed(in_a30, 64)],
            [to_signed(in_a01, 64), to_signed(in_a11, 64), to_signed(in_a21, 64), to_signed(in_a31, 64)],
        ]
        x = [
            to_signed(in_x0, 64),
            to_signed(in_x1, 64),
            to_signed(in_x2, 64),
            to_signed(in_x3, 64),
        ]

        if in_trans == 0:
            if in_uplo != 0:
                for j in range(4):
                    if x[j] != 0:
                        temp = x[j]
                        if j >= 1:
                            i = j - 1
                            x[i] = wrap64_signed(x[i] + temp * a[0][j])
                        if in_diag == 0:
                            x[j] = wrap64_signed(x[j] * a[1][j])
            else:
                for j in range(3, -1, -1):
                    if x[j] != 0:
                        temp = x[j]
                        if j <= 2:
                            i = j + 1
                            x[i] = wrap64_signed(x[i] + temp * a[1][j])
                        if in_diag == 0:
                            x[j] = wrap64_signed(x[j] * a[0][j])
        else:
            if in_uplo != 0:
                for j in range(3, -1, -1):
                    temp = x[j]
                    if in_diag == 0:
                        temp = wrap64_signed(temp * a[1][j])
                    if j >= 1:
                        i = j - 1
                        temp = wrap64_signed(temp + a[0][j] * x[i])
                    x[j] = temp
            else:
                for j in range(4):
                    temp = x[j]
                    if in_diag == 0:
                        temp = wrap64_signed(temp * a[0][j])
                    if j <= 2:
                        i = j + 1
                        temp = wrap64_signed(temp + a[1][j] * x[i])
                    x[j] = temp

        self.out_x0 = to_unsigned(x[0], 64)
        self.out_x1 = to_unsigned(x[1], 64)
        self.out_x2 = to_unsigned(x[2], 64)
        self.out_x3 = to_unsigned(x[3], 64)

        return {
            "out_x0": self.out_x0,
            "out_x1": self.out_x1,
            "out_x2": self.out_x2,
            "out_x3": self.out_x3,
        }