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

def sqrt_q32(x):
    root = 0
    for bit in range(47, -1, -1):
        trial = root | (1 << bit)
        if trial * trial <= x << 32:
            root = trial
    return root

def triangular(a, b, upper, unit):
    x = [0] * 4
    for step in range(4):
        i = 3 - step if upper else step
        acc = b[i]
        for k in range(4):
            if (upper and k > i) or (not upper and k < i):
                acc = signed64(acc - qmul(a[i][k], x[k]))
        x[i] = acc if unit else qdiv(acc, a[i][i])
    return x

class LuInverse4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a00, in_a01, in_a02, in_a03, in_a10, in_a11, in_a12, in_a13, in_a20, in_a21, in_a22, in_a23, in_a30, in_a31, in_a32, in_a33, in_ipiv0, in_ipiv1, in_ipiv2, in_ipiv3):
        a = [
            [signed64(in_a00), signed64(in_a01), signed64(in_a02), signed64(in_a03)],
            [signed64(in_a10), signed64(in_a11), signed64(in_a12), signed64(in_a13)],
            [signed64(in_a20), signed64(in_a21), signed64(in_a22), signed64(in_a23)],
            [signed64(in_a30), signed64(in_a31), signed64(in_a32), signed64(in_a33)],
        ]
        piv = [in_ipiv0, in_ipiv1, in_ipiv2, in_ipiv3]
        info = 0
        for k in range(4):
            if a[k][k] == 0 and info == 0:
                info = k + 1
        result = [[0] * 4 for _ in range(4)]
        if info:
            for r in range(4):
                for c in range(4):
                    result[r][c] = a[r][c]
        else:
            for c in range(4):
                b = [0] * 4
                b[c] = 1 << 32
                y = triangular(a, b, False, True)
                x = triangular(a, y, True, False)
                for r in range(4):
                    result[r][c] = x[r]
            for j in range(2, -1, -1):
                if 1 <= piv[j] <= 4:
                    jp = piv[j] - 1
                    for r in range(4):
                        temp = result[r][j]
                        result[r][j] = result[r][jp]
                        result[r][jp] = temp

        return {
            "out_a00": (result[0][0]) & ((1 << 64) - 1),
            "out_a01": (result[0][1]) & ((1 << 64) - 1),
            "out_a02": (result[0][2]) & ((1 << 64) - 1),
            "out_a03": (result[0][3]) & ((1 << 64) - 1),
            "out_a10": (result[1][0]) & ((1 << 64) - 1),
            "out_a11": (result[1][1]) & ((1 << 64) - 1),
            "out_a12": (result[1][2]) & ((1 << 64) - 1),
            "out_a13": (result[1][3]) & ((1 << 64) - 1),
            "out_a20": (result[2][0]) & ((1 << 64) - 1),
            "out_a21": (result[2][1]) & ((1 << 64) - 1),
            "out_a22": (result[2][2]) & ((1 << 64) - 1),
            "out_a23": (result[2][3]) & ((1 << 64) - 1),
            "out_a30": (result[3][0]) & ((1 << 64) - 1),
            "out_a31": (result[3][1]) & ((1 << 64) - 1),
            "out_a32": (result[3][2]) & ((1 << 64) - 1),
            "out_a33": (result[3][3]) & ((1 << 64) - 1),
            "out_info": (info) & ((1 << 3) - 1),
        }
