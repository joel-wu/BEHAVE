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

class LuSolve4x1:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_trans, in_a00, in_a01, in_a02, in_a03, in_a10, in_a11, in_a12, in_a13, in_a20, in_a21, in_a22, in_a23, in_a30, in_a31, in_a32, in_a33, in_ipiv0, in_ipiv1, in_ipiv2, in_ipiv3, in_b0, in_b1, in_b2, in_b3):
        a = [
            [signed64(in_a00), signed64(in_a01), signed64(in_a02), signed64(in_a03)],
            [signed64(in_a10), signed64(in_a11), signed64(in_a12), signed64(in_a13)],
            [signed64(in_a20), signed64(in_a21), signed64(in_a22), signed64(in_a23)],
            [signed64(in_a30), signed64(in_a31), signed64(in_a32), signed64(in_a33)],
        ]
        piv = [in_ipiv0, in_ipiv1, in_ipiv2, in_ipiv3]
        b = [signed64(in_b0), signed64(in_b1), signed64(in_b2), signed64(in_b3)]
        if in_trans == 0:
            for j in range(4):
                jp = piv[j]
                temp = b[j]
                b[j] = b[jp]
                b[jp] = temp
            y = triangular(a, b, False, True)
            x = triangular(a, y, True, False)
        else:
            at = [[0] * 4 for _ in range(4)]
            for r in range(4):
                for c in range(4):
                    at[r][c] = a[c][r]
            y = triangular(at, b, False, False)
            x = triangular(at, y, True, True)
            for j in range(3, -1, -1):
                jp = piv[j]
                temp = x[j]
                x[j] = x[jp]
                x[jp] = temp

        return {
            "out_x0": (x[0]) & ((1 << 64) - 1),
            "out_x1": (x[1]) & ((1 << 64) - 1),
            "out_x2": (x[2]) & ((1 << 64) - 1),
            "out_x3": (x[3]) & ((1 << 64) - 1),
        }
