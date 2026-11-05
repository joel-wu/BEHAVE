def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class OrmqrReflectorStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_tau,
        in_a1,
        in_a2,
        in_a3,
        in_c00,
        in_c01,
        in_c02,
        in_c03,
        in_c10,
        in_c11,
        in_c12,
        in_c13,
        in_c20,
        in_c21,
        in_c22,
        in_c23,
        in_c30,
        in_c31,
        in_c32,
        in_c33,
    ):
        tau = to_signed(in_tau, 32)
        v = [1, to_signed(in_a1, 32), to_signed(in_a2, 32), to_signed(in_a3, 32)]
        c = [
            [to_signed(in_c00, 32), to_signed(in_c01, 32), to_signed(in_c02, 32), to_signed(in_c03, 32)],
            [to_signed(in_c10, 32), to_signed(in_c11, 32), to_signed(in_c12, 32), to_signed(in_c13, 32)],
            [to_signed(in_c20, 32), to_signed(in_c21, 32), to_signed(in_c22, 32), to_signed(in_c23, 32)],
            [to_signed(in_c30, 32), to_signed(in_c31, 32), to_signed(in_c32, 32), to_signed(in_c33, 32)],
        ]

        out = [[0, 0, 0, 0] for _ in range(4)]

        if in_side == 0:
            w = [0, 0, 0, 0]
            for j in range(4):
                w[j] = v[0] * c[0][j] + v[1] * c[1][j] + v[2] * c[2][j] + v[3] * c[3][j]
            for r in range(4):
                for j in range(4):
                    out[r][j] = c[r][j] - tau * v[r] * w[j]
        else:
            w = [0, 0, 0, 0]
            for i in range(4):
                w[i] = c[i][0] * v[0] + c[i][1] * v[1] + c[i][2] * v[2] + c[i][3] * v[3]
            for i in range(4):
                for j in range(4):
                    out[i][j] = c[i][j] - tau * w[i] * v[j]

        return {
            "out_c00": to_unsigned(out[0][0], 32),
            "out_c01": to_unsigned(out[0][1], 32),
            "out_c02": to_unsigned(out[0][2], 32),
            "out_c03": to_unsigned(out[0][3], 32),
            "out_c10": to_unsigned(out[1][0], 32),
            "out_c11": to_unsigned(out[1][1], 32),
            "out_c12": to_unsigned(out[1][2], 32),
            "out_c13": to_unsigned(out[1][3], 32),
            "out_c20": to_unsigned(out[2][0], 32),
            "out_c21": to_unsigned(out[2][1], 32),
            "out_c22": to_unsigned(out[2][2], 32),
            "out_c23": to_unsigned(out[2][3], 32),
            "out_c30": to_unsigned(out[3][0], 32),
            "out_c31": to_unsigned(out[3][1], 32),
            "out_c32": to_unsigned(out[3][2], 32),
            "out_c33": to_unsigned(out[3][3], 32),
        }