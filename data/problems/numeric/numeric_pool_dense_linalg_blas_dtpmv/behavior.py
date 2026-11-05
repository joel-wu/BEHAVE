def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class TriangularPackedMatrixVector4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_uplo,
        in_trans,
        in_diag,
        in_ap0,
        in_ap1,
        in_ap2,
        in_ap3,
        in_ap4,
        in_ap5,
        in_ap6,
        in_ap7,
        in_ap8,
        in_ap9,
        in_x0,
        in_x1,
        in_x2,
        in_x3,
    ):
        ap = [
            to_signed(in_ap0, 64),
            to_signed(in_ap1, 64),
            to_signed(in_ap2, 64),
            to_signed(in_ap3, 64),
            to_signed(in_ap4, 64),
            to_signed(in_ap5, 64),
            to_signed(in_ap6, 64),
            to_signed(in_ap7, 64),
            to_signed(in_ap8, 64),
            to_signed(in_ap9, 64),
        ]
        x = [
            to_signed(in_x0, 64),
            to_signed(in_x1, 64),
            to_signed(in_x2, 64),
            to_signed(in_x3, 64),
        ]

        if in_trans == 0:
            if in_uplo == 0:
                if x[0] != 0 and in_diag == 0:
                    x[0] = to_signed(x[0] * ap[0], 64)
                if x[1] != 0:
                    temp = x[1]
                    x[0] = to_signed(x[0] + temp * ap[1], 64)
                    if in_diag == 0:
                        x[1] = to_signed(x[1] * ap[2], 64)
                if x[2] != 0:
                    temp = x[2]
                    x[0] = to_signed(x[0] + temp * ap[3], 64)
                    x[1] = to_signed(x[1] + temp * ap[4], 64)
                    if in_diag == 0:
                        x[2] = to_signed(x[2] * ap[5], 64)
                if x[3] != 0:
                    temp = x[3]
                    x[0] = to_signed(x[0] + temp * ap[6], 64)
                    x[1] = to_signed(x[1] + temp * ap[7], 64)
                    x[2] = to_signed(x[2] + temp * ap[8], 64)
                    if in_diag == 0:
                        x[3] = to_signed(x[3] * ap[9], 64)
            else:
                if x[3] != 0 and in_diag == 0:
                    x[3] = to_signed(x[3] * ap[9], 64)
                if x[2] != 0:
                    temp = x[2]
                    x[3] = to_signed(x[3] + temp * ap[8], 64)
                    if in_diag == 0:
                        x[2] = to_signed(x[2] * ap[7], 64)
                if x[1] != 0:
                    temp = x[1]
                    x[3] = to_signed(x[3] + temp * ap[6], 64)
                    x[2] = to_signed(x[2] + temp * ap[5], 64)
                    if in_diag == 0:
                        x[1] = to_signed(x[1] * ap[4], 64)
                if x[0] != 0:
                    temp = x[0]
                    x[3] = to_signed(x[3] + temp * ap[3], 64)
                    x[2] = to_signed(x[2] + temp * ap[2], 64)
                    x[1] = to_signed(x[1] + temp * ap[1], 64)
                    if in_diag == 0:
                        x[0] = to_signed(x[0] * ap[0], 64)
        else:
            if in_uplo == 0:
                temp = x[3]
                if in_diag == 0:
                    temp = to_signed(temp * ap[9], 64)
                temp = to_signed(temp + ap[8] * x[2], 64)
                temp = to_signed(temp + ap[7] * x[1], 64)
                temp = to_signed(temp + ap[6] * x[0], 64)
                x[3] = temp

                temp = x[2]
                if in_diag == 0:
                    temp = to_signed(temp * ap[5], 64)
                temp = to_signed(temp + ap[4] * x[1], 64)
                temp = to_signed(temp + ap[3] * x[0], 64)
                x[2] = temp

                temp = x[1]
                if in_diag == 0:
                    temp = to_signed(temp * ap[2], 64)
                temp = to_signed(temp + ap[1] * x[0], 64)
                x[1] = temp

                temp = x[0]
                if in_diag == 0:
                    temp = to_signed(temp * ap[0], 64)
                x[0] = temp
            else:
                temp = x[0]
                if in_diag == 0:
                    temp = to_signed(temp * ap[0], 64)
                temp = to_signed(temp + ap[1] * x[1], 64)
                temp = to_signed(temp + ap[2] * x[2], 64)
                temp = to_signed(temp + ap[3] * x[3], 64)
                x[0] = temp

                temp = x[1]
                if in_diag == 0:
                    temp = to_signed(temp * ap[4], 64)
                temp = to_signed(temp + ap[5] * x[2], 64)
                temp = to_signed(temp + ap[6] * x[3], 64)
                x[1] = temp

                temp = x[2]
                if in_diag == 0:
                    temp = to_signed(temp * ap[7], 64)
                temp = to_signed(temp + ap[8] * x[3], 64)
                x[2] = temp

                temp = x[3]
                if in_diag == 0:
                    temp = to_signed(temp * ap[9], 64)
                x[3] = temp

        return {
            "out_x0": to_unsigned(x[0], 64),
            "out_x1": to_unsigned(x[1], 64),
            "out_x2": to_unsigned(x[2], 64),
            "out_x3": to_unsigned(x[3], 64),
        }