def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero(n, d):
    an = n if n >= 0 else -n
    ad = d if d >= 0 else -d
    q = an // ad
    return q if (n >= 0) == (d >= 0) else -q


def wrap16(v):
    return to_signed(v, 16)


def mul_q8_8(p, q):
    return wrap16(trunc_div_toward_zero(p * q, 256))


def div_q8_8(n, d):
    return wrap16(trunc_div_toward_zero(n * 256, d))


class LuSolve4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a00, in_a01, in_a02, in_a03,
        in_a10, in_a11, in_a12, in_a13,
        in_a20, in_a21, in_a22, in_a23,
        in_a30, in_a31, in_a32, in_a33,
        in_b0, in_b1, in_b2, in_b3,
    ):
        matrix = [
            [to_signed(in_a00, 16), to_signed(in_a01, 16), to_signed(in_a02, 16), to_signed(in_a03, 16)],
            [to_signed(in_a10, 16), to_signed(in_a11, 16), to_signed(in_a12, 16), to_signed(in_a13, 16)],
            [to_signed(in_a20, 16), to_signed(in_a21, 16), to_signed(in_a22, 16), to_signed(in_a23, 16)],
            [to_signed(in_a30, 16), to_signed(in_a31, 16), to_signed(in_a32, 16), to_signed(in_a33, 16)],
        ]
        rhs = [to_signed(in_b0, 16), to_signed(in_b1, 16), to_signed(in_b2, 16), to_signed(in_b3, 16)]
        ipiv = [0, 0, 0, 0]
        info = 0

        for k in range(4):
            p = k
            best = matrix[k][k]
            best_abs = best if best >= 0 else -best
            for i in range(k + 1, 4):
                mag = matrix[i][k]
                mag_abs = mag if mag >= 0 else -mag
                if mag_abs > best_abs:
                    p = i
                    best_abs = mag_abs
            ipiv[k] = p
            if p != k:
                matrix[k], matrix[p] = matrix[p], matrix[k]
                rhs[k], rhs[p] = rhs[p], rhs[k]
            if matrix[k][k] == 0:
                info = k + 1
                break
            for i in range(k + 1, 4):
                matrix[i][k] = div_q8_8(matrix[i][k], matrix[k][k])
                for j in range(k + 1, 4):
                    matrix[i][j] = wrap16(matrix[i][j] - mul_q8_8(matrix[i][k], matrix[k][j]))

        y = [0, 0, 0, 0]
        x = [0, 0, 0, 0]
        if info == 0:
            y[0] = wrap16(rhs[0])
            y[1] = wrap16(rhs[1] - mul_q8_8(matrix[1][0], y[0]))
            y[2] = wrap16(rhs[2] - mul_q8_8(matrix[2][0], y[0]) - mul_q8_8(matrix[2][1], y[1]))
            y[3] = wrap16(rhs[3] - mul_q8_8(matrix[3][0], y[0]) - mul_q8_8(matrix[3][1], y[1]) - mul_q8_8(matrix[3][2], y[2]))

            if matrix[3][3] == 0:
                info = 4
            else:
                x[3] = div_q8_8(y[3], matrix[3][3])
                t2 = wrap16(y[2] - mul_q8_8(matrix[2][3], x[3]))
                if matrix[2][2] == 0:
                    info = 3
                else:
                    x[2] = div_q8_8(t2, matrix[2][2])
                    t1 = wrap16(y[1] - mul_q8_8(matrix[1][2], x[2]) - mul_q8_8(matrix[1][3], x[3]))
                    if matrix[1][1] == 0:
                        info = 2
                    else:
                        x[1] = div_q8_8(t1, matrix[1][1])
                        t0 = wrap16(y[0] - mul_q8_8(matrix[0][1], x[1]) - mul_q8_8(matrix[0][2], x[2]) - mul_q8_8(matrix[0][3], x[3]))
                        if matrix[0][0] == 0:
                            info = 1
                        else:
                            x[0] = div_q8_8(t0, matrix[0][0])

        if info != 0:
            return {
                "out_x0": 0,
                "out_x1": 0,
                "out_x2": 0,
                "out_x3": 0,
                "out_info": to_unsigned(info, 3),
            }
        return {
            "out_x0": to_unsigned(x[0], 16),
            "out_x1": to_unsigned(x[1], 16),
            "out_x2": to_unsigned(x[2], 16),
            "out_x3": to_unsigned(x[3], 16),
            "out_info": 0,
        }