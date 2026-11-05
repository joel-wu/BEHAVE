def signed64(x):
    x &= (1 << 64) - 1
    return x - (1 << 64) if x >= (1 << 63) else x

def qmul(x, y):
    return signed64((x * y) >> 32)

def qdiv(x, y):
    if y == 0:
        return 0
    n = x << 32
    q = abs(n) // abs(y)
    return signed64(-q if (n < 0) != (y < 0) else q)

class BandedLUFactor4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_ab_0_0, in_ab_0_1, in_ab_0_2, in_ab_0_3, in_ab_1_0, in_ab_1_1, in_ab_1_2, in_ab_1_3, in_ab_2_0, in_ab_2_1, in_ab_2_2, in_ab_2_3):
        a = [[0] * 4 for _ in range(4)]
        a[0][0] = signed64(in_ab_1_0)
        a[1][0] = signed64(in_ab_2_0)
        a[1][1] = signed64(in_ab_1_1)
        a[0][1] = signed64(in_ab_0_1)
        a[2][1] = signed64(in_ab_2_1)
        a[2][2] = signed64(in_ab_1_2)
        a[1][2] = signed64(in_ab_0_2)
        a[3][2] = signed64(in_ab_2_2)
        a[3][3] = signed64(in_ab_1_3)
        a[2][3] = signed64(in_ab_0_3)
        piv = [0] * 4
        info = 0
        ju = 0
        for j in range(4):
            p = j
            if j < 3 and abs(a[j+1][j]) > abs(a[j][j]):
                p = j + 1
            piv[j] = p + 1
            if a[p][j] == 0:
                if info == 0:
                    info = j + 1
            else:
                candidate = min(3, p + 1)
                if candidate > ju:
                    ju = candidate
                for c in range(j, ju + 1):
                    temp = a[j][c]
                    a[j][c] = a[p][c]
                    a[p][c] = temp
                if j < 3:
                    a[j+1][j] = qdiv(a[j+1][j], a[j][j])
                    for c in range(j+1, ju+1):
                        a[j+1][c] = signed64(a[j+1][c] - qmul(a[j+1][j], a[j][c]))
        return {
            "out_ab_0_0": (0) & ((1 << 64) - 1),
            "out_ab_0_1": (0) & ((1 << 64) - 1),
            "out_ab_0_2": (a[0][2]) & ((1 << 64) - 1),
            "out_ab_0_3": (a[1][3]) & ((1 << 64) - 1),
            "out_ab_1_0": (0) & ((1 << 64) - 1),
            "out_ab_1_1": (a[0][1]) & ((1 << 64) - 1),
            "out_ab_1_2": (a[1][2]) & ((1 << 64) - 1),
            "out_ab_1_3": (a[2][3]) & ((1 << 64) - 1),
            "out_ab_2_0": (a[0][0]) & ((1 << 64) - 1),
            "out_ab_2_1": (a[1][1]) & ((1 << 64) - 1),
            "out_ab_2_2": (a[2][2]) & ((1 << 64) - 1),
            "out_ab_2_3": (a[3][3]) & ((1 << 64) - 1),
            "out_ab_3_0": (a[1][0]) & ((1 << 64) - 1),
            "out_ab_3_1": (a[2][1]) & ((1 << 64) - 1),
            "out_ab_3_2": (a[3][2]) & ((1 << 64) - 1),
            "out_ab_3_3": (0) & ((1 << 64) - 1),
            "out_ipiv_0": (piv[0]) & ((1 << 3) - 1),
            "out_ipiv_1": (piv[1]) & ((1 << 3) - 1),
            "out_ipiv_2": (piv[2]) & ((1 << 3) - 1),
            "out_ipiv_3": (piv[3]) & ((1 << 3) - 1),
            "out_info": (info) & ((1 << 3) - 1),
        }
