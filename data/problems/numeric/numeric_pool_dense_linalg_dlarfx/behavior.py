def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap32(v):
    return to_signed(v, 32)


class HouseholderReflectorApply10:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_m,
        in_n,
        in_tau,
        in_v0, in_v1, in_v2, in_v3, in_v4,
        in_v5, in_v6, in_v7, in_v8, in_v9,
        in_c00, in_c01, in_c02, in_c03, in_c04, in_c05, in_c06, in_c07, in_c08, in_c09,
        in_c10, in_c11, in_c12, in_c13, in_c14, in_c15, in_c16, in_c17, in_c18, in_c19,
        in_c20, in_c21, in_c22, in_c23, in_c24, in_c25, in_c26, in_c27, in_c28, in_c29,
        in_c30, in_c31, in_c32, in_c33, in_c34, in_c35, in_c36, in_c37, in_c38, in_c39,
        in_c40, in_c41, in_c42, in_c43, in_c44, in_c45, in_c46, in_c47, in_c48, in_c49,
        in_c50, in_c51, in_c52, in_c53, in_c54, in_c55, in_c56, in_c57, in_c58, in_c59,
        in_c60, in_c61, in_c62, in_c63, in_c64, in_c65, in_c66, in_c67, in_c68, in_c69,
        in_c70, in_c71, in_c72, in_c73, in_c74, in_c75, in_c76, in_c77, in_c78, in_c79,
        in_c80, in_c81, in_c82, in_c83, in_c84, in_c85, in_c86, in_c87, in_c88, in_c89,
        in_c90, in_c91, in_c92, in_c93, in_c94, in_c95, in_c96, in_c97, in_c98, in_c99
    ):
        tau = to_signed(in_tau, 32)
        v = [
            to_signed(in_v0, 32), to_signed(in_v1, 32), to_signed(in_v2, 32), to_signed(in_v3, 32), to_signed(in_v4, 32),
            to_signed(in_v5, 32), to_signed(in_v6, 32), to_signed(in_v7, 32), to_signed(in_v8, 32), to_signed(in_v9, 32),
        ]
        c = [
            [to_signed(in_c00, 32), to_signed(in_c01, 32), to_signed(in_c02, 32), to_signed(in_c03, 32), to_signed(in_c04, 32), to_signed(in_c05, 32), to_signed(in_c06, 32), to_signed(in_c07, 32), to_signed(in_c08, 32), to_signed(in_c09, 32)],
            [to_signed(in_c10, 32), to_signed(in_c11, 32), to_signed(in_c12, 32), to_signed(in_c13, 32), to_signed(in_c14, 32), to_signed(in_c15, 32), to_signed(in_c16, 32), to_signed(in_c17, 32), to_signed(in_c18, 32), to_signed(in_c19, 32)],
            [to_signed(in_c20, 32), to_signed(in_c21, 32), to_signed(in_c22, 32), to_signed(in_c23, 32), to_signed(in_c24, 32), to_signed(in_c25, 32), to_signed(in_c26, 32), to_signed(in_c27, 32), to_signed(in_c28, 32), to_signed(in_c29, 32)],
            [to_signed(in_c30, 32), to_signed(in_c31, 32), to_signed(in_c32, 32), to_signed(in_c33, 32), to_signed(in_c34, 32), to_signed(in_c35, 32), to_signed(in_c36, 32), to_signed(in_c37, 32), to_signed(in_c38, 32), to_signed(in_c39, 32)],
            [to_signed(in_c40, 32), to_signed(in_c41, 32), to_signed(in_c42, 32), to_signed(in_c43, 32), to_signed(in_c44, 32), to_signed(in_c45, 32), to_signed(in_c46, 32), to_signed(in_c47, 32), to_signed(in_c48, 32), to_signed(in_c49, 32)],
            [to_signed(in_c50, 32), to_signed(in_c51, 32), to_signed(in_c52, 32), to_signed(in_c53, 32), to_signed(in_c54, 32), to_signed(in_c55, 32), to_signed(in_c56, 32), to_signed(in_c57, 32), to_signed(in_c58, 32), to_signed(in_c59, 32)],
            [to_signed(in_c60, 32), to_signed(in_c61, 32), to_signed(in_c62, 32), to_signed(in_c63, 32), to_signed(in_c64, 32), to_signed(in_c65, 32), to_signed(in_c66, 32), to_signed(in_c67, 32), to_signed(in_c68, 32), to_signed(in_c69, 32)],
            [to_signed(in_c70, 32), to_signed(in_c71, 32), to_signed(in_c72, 32), to_signed(in_c73, 32), to_signed(in_c74, 32), to_signed(in_c75, 32), to_signed(in_c76, 32), to_signed(in_c77, 32), to_signed(in_c78, 32), to_signed(in_c79, 32)],
            [to_signed(in_c80, 32), to_signed(in_c81, 32), to_signed(in_c82, 32), to_signed(in_c83, 32), to_signed(in_c84, 32), to_signed(in_c85, 32), to_signed(in_c86, 32), to_signed(in_c87, 32), to_signed(in_c88, 32), to_signed(in_c89, 32)],
            [to_signed(in_c90, 32), to_signed(in_c91, 32), to_signed(in_c92, 32), to_signed(in_c93, 32), to_signed(in_c94, 32), to_signed(in_c95, 32), to_signed(in_c96, 32), to_signed(in_c97, 32), to_signed(in_c98, 32), to_signed(in_c99, 32)],
        ]
        out = [row[:] for row in c]
        if tau != 0:
            if in_side == 0:
                t = [wrap32(tau * v[i]) for i in range(10)]
                for j in range(10):
                    if j < in_n:
                        s = wrap32(v[0] * c[0][j])
                        if in_m >= 2:
                            s = wrap32(s + wrap32(v[1] * c[1][j]))
                        if in_m >= 3:
                            s = wrap32(s + wrap32(v[2] * c[2][j]))
                        if in_m >= 4:
                            s = wrap32(s + wrap32(v[3] * c[3][j]))
                        if in_m >= 5:
                            s = wrap32(s + wrap32(v[4] * c[4][j]))
                        if in_m >= 6:
                            s = wrap32(s + wrap32(v[5] * c[5][j]))
                        if in_m >= 7:
                            s = wrap32(s + wrap32(v[6] * c[6][j]))
                        if in_m >= 8:
                            s = wrap32(s + wrap32(v[7] * c[7][j]))
                        if in_m >= 9:
                            s = wrap32(s + wrap32(v[8] * c[8][j]))
                        if in_m >= 10:
                            s = wrap32(s + wrap32(v[9] * c[9][j]))
                        if in_m >= 1:
                            out[0][j] = wrap32(c[0][j] - wrap32(s * t[0]))
                        if in_m >= 2:
                            out[1][j] = wrap32(c[1][j] - wrap32(s * t[1]))
                        if in_m >= 3:
                            out[2][j] = wrap32(c[2][j] - wrap32(s * t[2]))
                        if in_m >= 4:
                            out[3][j] = wrap32(c[3][j] - wrap32(s * t[3]))
                        if in_m >= 5:
                            out[4][j] = wrap32(c[4][j] - wrap32(s * t[4]))
                        if in_m >= 6:
                            out[5][j] = wrap32(c[5][j] - wrap32(s * t[5]))
                        if in_m >= 7:
                            out[6][j] = wrap32(c[6][j] - wrap32(s * t[6]))
                        if in_m >= 8:
                            out[7][j] = wrap32(c[7][j] - wrap32(s * t[7]))
                        if in_m >= 9:
                            out[8][j] = wrap32(c[8][j] - wrap32(s * t[8]))
                        if in_m >= 10:
                            out[9][j] = wrap32(c[9][j] - wrap32(s * t[9]))
            else:
                t = [wrap32(tau * v[i]) for i in range(10)]
                for j in range(10):
                    if j < in_m:
                        s = wrap32(v[0] * c[j][0])
                        if in_n >= 2:
                            s = wrap32(s + wrap32(v[1] * c[j][1]))
                        if in_n >= 3:
                            s = wrap32(s + wrap32(v[2] * c[j][2]))
                        if in_n >= 4:
                            s = wrap32(s + wrap32(v[3] * c[j][3]))
                        if in_n >= 5:
                            s = wrap32(s + wrap32(v[4] * c[j][4]))
                        if in_n >= 6:
                            s = wrap32(s + wrap32(v[5] * c[j][5]))
                        if in_n >= 7:
                            s = wrap32(s + wrap32(v[6] * c[j][6]))
                        if in_n >= 8:
                            s = wrap32(s + wrap32(v[7] * c[j][7]))
                        if in_n >= 9:
                            s = wrap32(s + wrap32(v[8] * c[j][8]))
                        if in_n >= 10:
                            s = wrap32(s + wrap32(v[9] * c[j][9]))
                        if in_n >= 1:
                            out[j][0] = wrap32(c[j][0] - wrap32(s * t[0]))
                        if in_n >= 2:
                            out[j][1] = wrap32(c[j][1] - wrap32(s * t[1]))
                        if in_n >= 3:
                            out[j][2] = wrap32(c[j][2] - wrap32(s * t[2]))
                        if in_n >= 4:
                            out[j][3] = wrap32(c[j][3] - wrap32(s * t[3]))
                        if in_n >= 5:
                            out[j][4] = wrap32(c[j][4] - wrap32(s * t[4]))
                        if in_n >= 6:
                            out[j][5] = wrap32(c[j][5] - wrap32(s * t[5]))
                        if in_n >= 7:
                            out[j][6] = wrap32(c[j][6] - wrap32(s * t[6]))
                        if in_n >= 8:
                            out[j][7] = wrap32(c[j][7] - wrap32(s * t[7]))
                        if in_n >= 9:
                            out[j][8] = wrap32(c[j][8] - wrap32(s * t[8]))
                        if in_n >= 10:
                            out[j][9] = wrap32(c[j][9] - wrap32(s * t[9]))
        return {
            "out_c00": to_unsigned(out[0][0], 32), "out_c01": to_unsigned(out[0][1], 32), "out_c02": to_unsigned(out[0][2], 32), "out_c03": to_unsigned(out[0][3], 32), "out_c04": to_unsigned(out[0][4], 32),
            "out_c05": to_unsigned(out[0][5], 32), "out_c06": to_unsigned(out[0][6], 32), "out_c07": to_unsigned(out[0][7], 32), "out_c08": to_unsigned(out[0][8], 32), "out_c09": to_unsigned(out[0][9], 32),
            "out_c10": to_unsigned(out[1][0], 32), "out_c11": to_unsigned(out[1][1], 32), "out_c12": to_unsigned(out[1][2], 32), "out_c13": to_unsigned(out[1][3], 32), "out_c14": to_unsigned(out[1][4], 32),
            "out_c15": to_unsigned(out[1][5], 32), "out_c16": to_unsigned(out[1][6], 32), "out_c17": to_unsigned(out[1][7], 32), "out_c18": to_unsigned(out[1][8], 32), "out_c19": to_unsigned(out[1][9], 32),
            "out_c20": to_unsigned(out[2][0], 32), "out_c21": to_unsigned(out[2][1], 32), "out_c22": to_unsigned(out[2][2], 32), "out_c23": to_unsigned(out[2][3], 32), "out_c24": to_unsigned(out[2][4], 32),
            "out_c25": to_unsigned(out[2][5], 32), "out_c26": to_unsigned(out[2][6], 32), "out_c27": to_unsigned(out[2][7], 32), "out_c28": to_unsigned(out[2][8], 32), "out_c29": to_unsigned(out[2][9], 32),
            "out_c30": to_unsigned(out[3][0], 32), "out_c31": to_unsigned(out[3][1], 32), "out_c32": to_unsigned(out[3][2], 32), "out_c33": to_unsigned(out[3][3], 32), "out_c34": to_unsigned(out[3][4], 32),
            "out_c35": to_unsigned(out[3][5], 32), "out_c36": to_unsigned(out[3][6], 32), "out_c37": to_unsigned(out[3][7], 32), "out_c38": to_unsigned(out[3][8], 32), "out_c39": to_unsigned(out[3][9], 32),
            "out_c40": to_unsigned(out[4][0], 32), "out_c41": to_unsigned(out[4][1], 32), "out_c42": to_unsigned(out[4][2], 32), "out_c43": to_unsigned(out[4][3], 32), "out_c44": to_unsigned(out[4][4], 32),
            "out_c45": to_unsigned(out[4][5], 32), "out_c46": to_unsigned(out[4][6], 32), "out_c47": to_unsigned(out[4][7], 32), "out_c48": to_unsigned(out[4][8], 32), "out_c49": to_unsigned(out[4][9], 32),
            "out_c50": to_unsigned(out[5][0], 32), "out_c51": to_unsigned(out[5][1], 32), "out_c52": to_unsigned(out[5][2], 32), "out_c53": to_unsigned(out[5][3], 32), "out_c54": to_unsigned(out[5][4], 32),
            "out_c55": to_unsigned(out[5][5], 32), "out_c56": to_unsigned(out[5][6], 32), "out_c57": to_unsigned(out[5][7], 32), "out_c58": to_unsigned(out[5][8], 32), "out_c59": to_unsigned(out[5][9], 32),
            "out_c60": to_unsigned(out[6][0], 32), "out_c61": to_unsigned(out[6][1], 32), "out_c62": to_unsigned(out[6][2], 32), "out_c63": to_unsigned(out[6][3], 32), "out_c64": to_unsigned(out[6][4], 32),
            "out_c65": to_unsigned(out[6][5], 32), "out_c66": to_unsigned(out[6][6], 32), "out_c67": to_unsigned(out[6][7], 32), "out_c68": to_unsigned(out[6][8], 32), "out_c69": to_unsigned(out[6][9], 32),
            "out_c70": to_unsigned(out[7][0], 32), "out_c71": to_unsigned(out[7][1], 32), "out_c72": to_unsigned(out[7][2], 32), "out_c73": to_unsigned(out[7][3], 32), "out_c74": to_unsigned(out[7][4], 32),
            "out_c75": to_unsigned(out[7][5], 32), "out_c76": to_unsigned(out[7][6], 32), "out_c77": to_unsigned(out[7][7], 32), "out_c78": to_unsigned(out[7][8], 32), "out_c79": to_unsigned(out[7][9], 32),
            "out_c80": to_unsigned(out[8][0], 32), "out_c81": to_unsigned(out[8][1], 32), "out_c82": to_unsigned(out[8][2], 32), "out_c83": to_unsigned(out[8][3], 32), "out_c84": to_unsigned(out[8][4], 32),
            "out_c85": to_unsigned(out[8][5], 32), "out_c86": to_unsigned(out[8][6], 32), "out_c87": to_unsigned(out[8][7], 32), "out_c88": to_unsigned(out[8][8], 32), "out_c89": to_unsigned(out[8][9], 32),
            "out_c90": to_unsigned(out[9][0], 32), "out_c91": to_unsigned(out[9][1], 32), "out_c92": to_unsigned(out[9][2], 32), "out_c93": to_unsigned(out[9][3], 32), "out_c94": to_unsigned(out[9][4], 32),
            "out_c95": to_unsigned(out[9][5], 32), "out_c96": to_unsigned(out[9][6], 32), "out_c97": to_unsigned(out[9][7], 32), "out_c98": to_unsigned(out[9][8], 32), "out_c99": to_unsigned(out[9][9], 32),
        }