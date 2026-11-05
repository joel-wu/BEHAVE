def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class WinogradF43Full:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_d00, in_d01, in_d02, in_d03, in_d04, in_d05,
        in_d10, in_d11, in_d12, in_d13, in_d14, in_d15,
        in_d20, in_d21, in_d22, in_d23, in_d24, in_d25,
        in_d30, in_d31, in_d32, in_d33, in_d34, in_d35,
        in_d40, in_d41, in_d42, in_d43, in_d44, in_d45,
        in_d50, in_d51, in_d52, in_d53, in_d54, in_d55,
        in_g00, in_g01, in_g02,
        in_g10, in_g11, in_g12,
        in_g20, in_g21, in_g22
    ):
        d = [
            [to_signed(in_d00, 16), to_signed(in_d01, 16), to_signed(in_d02, 16), to_signed(in_d03, 16), to_signed(in_d04, 16), to_signed(in_d05, 16)],
            [to_signed(in_d10, 16), to_signed(in_d11, 16), to_signed(in_d12, 16), to_signed(in_d13, 16), to_signed(in_d14, 16), to_signed(in_d15, 16)],
            [to_signed(in_d20, 16), to_signed(in_d21, 16), to_signed(in_d22, 16), to_signed(in_d23, 16), to_signed(in_d24, 16), to_signed(in_d25, 16)],
            [to_signed(in_d30, 16), to_signed(in_d31, 16), to_signed(in_d32, 16), to_signed(in_d33, 16), to_signed(in_d34, 16), to_signed(in_d35, 16)],
            [to_signed(in_d40, 16), to_signed(in_d41, 16), to_signed(in_d42, 16), to_signed(in_d43, 16), to_signed(in_d44, 16), to_signed(in_d45, 16)],
            [to_signed(in_d50, 16), to_signed(in_d51, 16), to_signed(in_d52, 16), to_signed(in_d53, 16), to_signed(in_d54, 16), to_signed(in_d55, 16)],
        ]
        g = [
            [to_signed(in_g00, 16), to_signed(in_g01, 16), to_signed(in_g02, 16)],
            [to_signed(in_g10, 16), to_signed(in_g11, 16), to_signed(in_g12, 16)],
            [to_signed(in_g20, 16), to_signed(in_g21, 16), to_signed(in_g22, 16)],
        ]

        y00 = d[0][0] * g[0][0] + d[0][1] * g[0][1] + d[0][2] * g[0][2] + d[1][0] * g[1][0] + d[1][1] * g[1][1] + d[1][2] * g[1][2] + d[2][0] * g[2][0] + d[2][1] * g[2][1] + d[2][2] * g[2][2]
        y01 = d[0][1] * g[0][0] + d[0][2] * g[0][1] + d[0][3] * g[0][2] + d[1][1] * g[1][0] + d[1][2] * g[1][1] + d[1][3] * g[1][2] + d[2][1] * g[2][0] + d[2][2] * g[2][1] + d[2][3] * g[2][2]
        y02 = d[0][2] * g[0][0] + d[0][3] * g[0][1] + d[0][4] * g[0][2] + d[1][2] * g[1][0] + d[1][3] * g[1][1] + d[1][4] * g[1][2] + d[2][2] * g[2][0] + d[2][3] * g[2][1] + d[2][4] * g[2][2]
        y03 = d[0][3] * g[0][0] + d[0][4] * g[0][1] + d[0][5] * g[0][2] + d[1][3] * g[1][0] + d[1][4] * g[1][1] + d[1][5] * g[1][2] + d[2][3] * g[2][0] + d[2][4] * g[2][1] + d[2][5] * g[2][2]

        y10 = d[1][0] * g[0][0] + d[1][1] * g[0][1] + d[1][2] * g[0][2] + d[2][0] * g[1][0] + d[2][1] * g[1][1] + d[2][2] * g[1][2] + d[3][0] * g[2][0] + d[3][1] * g[2][1] + d[3][2] * g[2][2]
        y11 = d[1][1] * g[0][0] + d[1][2] * g[0][1] + d[1][3] * g[0][2] + d[2][1] * g[1][0] + d[2][2] * g[1][1] + d[2][3] * g[1][2] + d[3][1] * g[2][0] + d[3][2] * g[2][1] + d[3][3] * g[2][2]
        y12 = d[1][2] * g[0][0] + d[1][3] * g[0][1] + d[1][4] * g[0][2] + d[2][2] * g[1][0] + d[2][3] * g[1][1] + d[2][4] * g[1][2] + d[3][2] * g[2][0] + d[3][3] * g[2][1] + d[3][4] * g[2][2]
        y13 = d[1][3] * g[0][0] + d[1][4] * g[0][1] + d[1][5] * g[0][2] + d[2][3] * g[1][0] + d[2][4] * g[1][1] + d[2][5] * g[1][2] + d[3][3] * g[2][0] + d[3][4] * g[2][1] + d[3][5] * g[2][2]

        y20 = d[2][0] * g[0][0] + d[2][1] * g[0][1] + d[2][2] * g[0][2] + d[3][0] * g[1][0] + d[3][1] * g[1][1] + d[3][2] * g[1][2] + d[4][0] * g[2][0] + d[4][1] * g[2][1] + d[4][2] * g[2][2]
        y21 = d[2][1] * g[0][0] + d[2][2] * g[0][1] + d[2][3] * g[0][2] + d[3][1] * g[1][0] + d[3][2] * g[1][1] + d[3][3] * g[1][2] + d[4][1] * g[2][0] + d[4][2] * g[2][1] + d[4][3] * g[2][2]
        y22 = d[2][2] * g[0][0] + d[2][3] * g[0][1] + d[2][4] * g[0][2] + d[3][2] * g[1][0] + d[3][3] * g[1][1] + d[3][4] * g[1][2] + d[4][2] * g[2][0] + d[4][3] * g[2][1] + d[4][4] * g[2][2]
        y23 = d[2][3] * g[0][0] + d[2][4] * g[0][1] + d[2][5] * g[0][2] + d[3][3] * g[1][0] + d[3][4] * g[1][1] + d[3][5] * g[1][2] + d[4][3] * g[2][0] + d[4][4] * g[2][1] + d[4][5] * g[2][2]

        y30 = d[3][0] * g[0][0] + d[3][1] * g[0][1] + d[3][2] * g[0][2] + d[4][0] * g[1][0] + d[4][1] * g[1][1] + d[4][2] * g[1][2] + d[5][0] * g[2][0] + d[5][1] * g[2][1] + d[5][2] * g[2][2]
        y31 = d[3][1] * g[0][0] + d[3][2] * g[0][1] + d[3][3] * g[0][2] + d[4][1] * g[1][0] + d[4][2] * g[1][1] + d[4][3] * g[1][2] + d[5][1] * g[2][0] + d[5][2] * g[2][1] + d[5][3] * g[2][2]
        y32 = d[3][2] * g[0][0] + d[3][3] * g[0][1] + d[3][4] * g[0][2] + d[4][2] * g[1][0] + d[4][3] * g[1][1] + d[4][4] * g[1][2] + d[5][2] * g[2][0] + d[5][3] * g[2][1] + d[5][4] * g[2][2]
        y33 = d[3][3] * g[0][0] + d[3][4] * g[0][1] + d[3][5] * g[0][2] + d[4][3] * g[1][0] + d[4][4] * g[1][1] + d[4][5] * g[1][2] + d[5][3] * g[2][0] + d[5][4] * g[2][1] + d[5][5] * g[2][2]

        return {
            "out_y00": to_unsigned(y00, 32),
            "out_y01": to_unsigned(y01, 32),
            "out_y02": to_unsigned(y02, 32),
            "out_y03": to_unsigned(y03, 32),
            "out_y10": to_unsigned(y10, 32),
            "out_y11": to_unsigned(y11, 32),
            "out_y12": to_unsigned(y12, 32),
            "out_y13": to_unsigned(y13, 32),
            "out_y20": to_unsigned(y20, 32),
            "out_y21": to_unsigned(y21, 32),
            "out_y22": to_unsigned(y22, 32),
            "out_y23": to_unsigned(y23, 32),
            "out_y30": to_unsigned(y30, 32),
            "out_y31": to_unsigned(y31, 32),
            "out_y32": to_unsigned(y32, 32),
            "out_y33": to_unsigned(y33, 32),
        }