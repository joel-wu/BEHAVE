def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap16(v):
    return to_unsigned(v, 16)


def add16(a, b):
    return wrap16(to_signed(a, 16) + to_signed(b, 16))


def sub16(a, b):
    return wrap16(to_signed(a, 16) - to_signed(b, 16))


def dot2(a0, a1, b0, b1):
    return wrap16(to_signed(a0, 16) * to_signed(b0, 16) + to_signed(a1, 16) * to_signed(b1, 16))


class BlockReflectorApply4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side,
        in_trans,
        in_direct,
        in_storev,
        in_v00,
        in_v01,
        in_v10,
        in_v11,
        in_v20,
        in_v21,
        in_v30,
        in_v31,
        in_t00,
        in_t01,
        in_t10,
        in_t11,
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
        c = [
            [in_c00, in_c01, in_c02, in_c03],
            [in_c10, in_c11, in_c12, in_c13],
            [in_c20, in_c21, in_c22, in_c23],
            [in_c30, in_c31, in_c32, in_c33],
        ]

        if in_direct == 0:
            top_t = [[in_t00, in_t01], [0, in_t11]]
        else:
            top_t = [[in_t00, 0], [in_t10, in_t11]]

        if (in_side == 0 and in_trans == 0) or (in_side == 1 and in_trans == 1):
            tmul = [[top_t[0][0], top_t[1][0]], [top_t[0][1], top_t[1][1]]]
        else:
            tmul = [[top_t[0][0], top_t[0][1]], [top_t[1][0], top_t[1][1]]]

        if in_side == 0 and in_direct == 0:
            if in_storev == 0:
                a = [[1, 0], [in_v10, 1]]
                b = [[in_v20, in_v21], [in_v30, in_v31]]
            else:
                a = [[1, in_v10], [0, 1]]
                b = [[in_v20, in_v30], [in_v21, in_v31]]

            w3 = []
            for j in range(4):
                w1j0 = dot2(c[0][j], c[1][j], a[0][0], a[1][0])
                w1j1 = dot2(c[0][j], c[1][j], a[0][1], a[1][1])
                xj0 = dot2(c[2][j], c[3][j], b[0][0], b[1][0])
                xj1 = dot2(c[2][j], c[3][j], b[0][1], b[1][1])
                w2j0 = add16(w1j0, xj0)
                w2j1 = add16(w1j1, xj1)
                w3j0 = dot2(w2j0, w2j1, tmul[0][0], tmul[1][0])
                w3j1 = dot2(w2j0, w2j1, tmul[0][1], tmul[1][1])
                w3.append([w3j0, w3j1])

            out = [[0] * 4 for _ in range(4)]
            for j in range(4):
                y0j = dot2(b[0][0], b[0][1], w3[j][0], w3[j][1])
                y1j = dot2(b[1][0], b[1][1], w3[j][0], w3[j][1])
                z0j = dot2(w3[j][0], w3[j][1], a[0][0], a[0][1])
                z1j = dot2(w3[j][0], w3[j][1], a[1][0], a[1][1])
                out[0][j] = sub16(c[0][j], z0j)
                out[1][j] = sub16(c[1][j], z1j)
                out[2][j] = sub16(c[2][j], y0j)
                out[3][j] = sub16(c[3][j], y1j)
        elif in_side == 0 and in_direct == 1:
            if in_storev == 0:
                a = [[in_v00, in_v01], [in_v10, in_v11]]
                b = [[1, in_v21], [0, 1]]
            else:
                a = [[in_v00, in_v10], [in_v01, in_v11]]
                b = [[1, 0], [in_v21, 1]]

            w3 = []
            for j in range(4):
                w1j0 = dot2(c[2][j], c[3][j], b[0][0], b[1][0])
                w1j1 = dot2(c[2][j], c[3][j], b[0][1], b[1][1])
                xj0 = dot2(c[0][j], c[1][j], a[0][0], a[1][0])
                xj1 = dot2(c[0][j], c[1][j], a[0][1], a[1][1])
                w2j0 = add16(w1j0, xj0)
                w2j1 = add16(w1j1, xj1)
                w3j0 = dot2(w2j0, w2j1, tmul[0][0], tmul[1][0])
                w3j1 = dot2(w2j0, w2j1, tmul[0][1], tmul[1][1])
                w3.append([w3j0, w3j1])

            out = [[0] * 4 for _ in range(4)]
            for j in range(4):
                y0j = dot2(a[0][0], a[0][1], w3[j][0], w3[j][1])
                y1j = dot2(a[1][0], a[1][1], w3[j][0], w3[j][1])
                z0j = dot2(w3[j][0], w3[j][1], b[0][0], b[0][1])
                z1j = dot2(w3[j][0], w3[j][1], b[1][0], b[1][1])
                out[0][j] = sub16(c[0][j], y0j)
                out[1][j] = sub16(c[1][j], y1j)
                out[2][j] = sub16(c[2][j], z0j)
                out[3][j] = sub16(c[3][j], z1j)
        elif in_side == 1 and in_direct == 0:
            if in_storev == 0:
                a = [[1, 0], [in_v10, 1]]
                b = [[in_v20, in_v21], [in_v30, in_v31]]
            else:
                a = [[1, in_v10], [0, 1]]
                b = [[in_v20, in_v30], [in_v21, in_v31]]

            out = [[0] * 4 for _ in range(4)]
            for i in range(4):
                w1i0 = dot2(c[i][0], c[i][1], a[0][0], a[1][0])
                w1i1 = dot2(c[i][0], c[i][1], a[0][1], a[1][1])
                xi0 = dot2(c[i][2], c[i][3], b[0][0], b[1][0])
                xi1 = dot2(c[i][2], c[i][3], b[0][1], b[1][1])
                w2i0 = add16(w1i0, xi0)
                w2i1 = add16(w1i1, xi1)
                w3i0 = dot2(w2i0, w2i1, tmul[0][0], tmul[1][0])
                w3i1 = dot2(w2i0, w2i1, tmul[0][1], tmul[1][1])
                y0i = dot2(w3i0, w3i1, b[0][0], b[0][1])
                y1i = dot2(w3i0, w3i1, b[1][0], b[1][1])
                z0i = dot2(w3i0, w3i1, a[0][0], a[0][1])
                z1i = dot2(w3i0, w3i1, a[1][0], a[1][1])
                out[i][0] = sub16(c[i][0], z0i)
                out[i][1] = sub16(c[i][1], z1i)
                out[i][2] = sub16(c[i][2], y0i)
                out[i][3] = sub16(c[i][3], y1i)
        else:
            if in_storev == 0:
                a = [[in_v00, in_v01], [in_v10, in_v11]]
                b = [[1, in_v21], [0, 1]]
            else:
                a = [[in_v00, in_v10], [in_v01, in_v11]]
                b = [[1, 0], [in_v21, 1]]

            out = [[0] * 4 for _ in range(4)]
            for i in range(4):
                w1i0 = dot2(c[i][2], c[i][3], b[0][0], b[1][0])
                w1i1 = dot2(c[i][2], c[i][3], b[0][1], b[1][1])
                xi0 = dot2(c[i][0], c[i][1], a[0][0], a[1][0])
                xi1 = dot2(c[i][0], c[i][1], a[0][1], a[1][1])
                w2i0 = add16(w1i0, xi0)
                w2i1 = add16(w1i1, xi1)
                w3i0 = dot2(w2i0, w2i1, tmul[0][0], tmul[1][0])
                w3i1 = dot2(w2i0, w2i1, tmul[0][1], tmul[1][1])
                y0i = dot2(a[0][0], a[0][1], w3i0, w3i1)
                y1i = dot2(a[1][0], a[1][1], w3i0, w3i1)
                z0i = dot2(w3i0, w3i1, b[0][0], b[0][1])
                z1i = dot2(w3i0, w3i1, b[1][0], b[1][1])
                out[i][0] = sub16(c[i][0], y0i)
                out[i][1] = sub16(c[i][1], y1i)
                out[i][2] = sub16(c[i][2], z0i)
                out[i][3] = sub16(c[i][3], z1i)

        return {
            "out_c00": to_unsigned(out[0][0], 16),
            "out_c01": to_unsigned(out[0][1], 16),
            "out_c02": to_unsigned(out[0][2], 16),
            "out_c03": to_unsigned(out[0][3], 16),
            "out_c10": to_unsigned(out[1][0], 16),
            "out_c11": to_unsigned(out[1][1], 16),
            "out_c12": to_unsigned(out[1][2], 16),
            "out_c13": to_unsigned(out[1][3], 16),
            "out_c20": to_unsigned(out[2][0], 16),
            "out_c21": to_unsigned(out[2][1], 16),
            "out_c22": to_unsigned(out[2][2], 16),
            "out_c23": to_unsigned(out[2][3], 16),
            "out_c30": to_unsigned(out[3][0], 16),
            "out_c31": to_unsigned(out[3][1], 16),
            "out_c32": to_unsigned(out[3][2], 16),
            "out_c33": to_unsigned(out[3][3], 16),
        }