class ComplexTriangularSolve2x2:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_b00_re = 0
        self.out_b00_im = 0
        self.out_b01_re = 0
        self.out_b01_im = 0
        self.out_b10_re = 0
        self.out_b10_im = 0
        self.out_b11_re = 0
        self.out_b11_im = 0

    def process(
        self,
        in_side,
        in_uplo,
        in_transa,
        in_diag,
        in_alpha_re,
        in_alpha_im,
        in_a00_re,
        in_a00_im,
        in_a01_re,
        in_a01_im,
        in_a10_re,
        in_a10_im,
        in_a11_re,
        in_a11_im,
        in_b00_re,
        in_b00_im,
        in_b01_re,
        in_b01_im,
        in_b10_re,
        in_b10_im,
        in_b11_re,
        in_b11_im,
    ):
        def to_unsigned(v, b):
            return v & ((1 << b) - 1)

        def to_signed(v, b):
            v &= (1 << b) - 1
            if v & (1 << (b - 1)):
                v -= 1 << b
            return v

        def cadd(x, y):
            return (x[0] + y[0], x[1] + y[1])

        def csub(x, y):
            return (x[0] - y[0], x[1] - y[1])

        def cmul(x, y):
            return (x[0] * y[0] - x[1] * y[1], x[0] * y[1] + x[1] * y[0])

        def trunc_div(n, d):
            if d == 0:
                return 0
            q = abs(n) // abs(d)
            return q if (n >= 0) == (d >= 0) else -q

        def cdiv(n, d):
            den = d[0] * d[0] + d[1] * d[1]
            if den == 0:
                return (0, 0)
            q_re = trunc_div(n[0] * d[0] + n[1] * d[1], den)
            q_im = trunc_div(n[1] * d[0] - n[0] * d[1], den)
            return (q_re, q_im)

        def cconj(x):
            return (x[0], -x[1])

        def czero(x):
            return x[0] == 0 and x[1] == 0

        def cone(x):
            return x[0] == 1 and x[1] == 0

        alpha = (to_signed(in_alpha_re, 16), to_signed(in_alpha_im, 16))
        a00 = (to_signed(in_a00_re, 16), to_signed(in_a00_im, 16))
        a01 = (to_signed(in_a01_re, 16), to_signed(in_a01_im, 16))
        a10 = (to_signed(in_a10_re, 16), to_signed(in_a10_im, 16))
        a11 = (to_signed(in_a11_re, 16), to_signed(in_a11_im, 16))
        b = [
            [(to_signed(in_b00_re, 16), to_signed(in_b00_im, 16)), (to_signed(in_b01_re, 16), to_signed(in_b01_im, 16))],
            [(to_signed(in_b10_re, 16), to_signed(in_b10_im, 16)), (to_signed(in_b11_re, 16), to_signed(in_b11_im, 16))],
        ]

        if czero(alpha):
            b = [[(0, 0), (0, 0)], [(0, 0), (0, 0)]]
        elif in_side == 0:
            if in_transa == 0:
                if in_uplo == 0:
                    for j in range(2):
                        if not cone(alpha):
                            b[0][j] = cmul(alpha, b[0][j])
                            b[1][j] = cmul(alpha, b[1][j])
                        if not czero(b[1][j]):
                            if in_diag == 1:
                                b[1][j] = cdiv(b[1][j], a11)
                            b[0][j] = csub(b[0][j], cmul(b[1][j], a01))
                        if not czero(b[0][j]):
                            if in_diag == 1:
                                b[0][j] = cdiv(b[0][j], a00)
                else:
                    for j in range(2):
                        if not cone(alpha):
                            b[0][j] = cmul(alpha, b[0][j])
                            b[1][j] = cmul(alpha, b[1][j])
                        if not czero(b[0][j]):
                            if in_diag == 1:
                                b[0][j] = cdiv(b[0][j], a00)
                            b[1][j] = csub(b[1][j], cmul(b[0][j], a10))
                        if not czero(b[1][j]):
                            if in_diag == 1:
                                b[1][j] = cdiv(b[1][j], a11)
            else:
                conj = in_transa != 1
                if in_uplo == 0:
                    for j in range(2):
                        temp = cmul(alpha, b[0][j])
                        if in_diag == 1:
                            temp = cdiv(temp, cconj(a00) if conj else a00)
                        b[0][j] = temp
                        temp = cmul(alpha, b[1][j])
                        temp = csub(temp, cmul(cconj(a01) if conj else a01, b[0][j]))
                        if in_diag == 1:
                            temp = cdiv(temp, cconj(a11) if conj else a11)
                        b[1][j] = temp
                else:
                    for j in range(2):
                        temp = cmul(alpha, b[1][j])
                        if in_diag == 1:
                            temp = cdiv(temp, cconj(a11) if conj else a11)
                        b[1][j] = temp
                        temp = cmul(alpha, b[0][j])
                        temp = csub(temp, cmul(cconj(a10) if conj else a10, b[1][j]))
                        if in_diag == 1:
                            temp = cdiv(temp, cconj(a00) if conj else a00)
                        b[0][j] = temp
        else:
            if in_transa == 0:
                if in_uplo == 0:
                    if not cone(alpha):
                        b[0][0] = cmul(alpha, b[0][0])
                        b[1][0] = cmul(alpha, b[1][0])
                    if in_diag == 1:
                        b[0][0] = cdiv(b[0][0], a00)
                        b[1][0] = cdiv(b[1][0], a00)
                    if not cone(alpha):
                        b[0][1] = cmul(alpha, b[0][1])
                        b[1][1] = cmul(alpha, b[1][1])
                    if not czero(a01):
                        b[0][1] = csub(b[0][1], cmul(a01, b[0][0]))
                        b[1][1] = csub(b[1][1], cmul(a01, b[1][0]))
                    if in_diag == 1:
                        b[0][1] = cdiv(b[0][1], a11)
                        b[1][1] = cdiv(b[1][1], a11)
                else:
                    if not cone(alpha):
                        b[0][1] = cmul(alpha, b[0][1])
                        b[1][1] = cmul(alpha, b[1][1])
                    if in_diag == 1:
                        b[0][1] = cdiv(b[0][1], a11)
                        b[1][1] = cdiv(b[1][1], a11)
                    if not cone(alpha):
                        b[0][0] = cmul(alpha, b[0][0])
                        b[1][0] = cmul(alpha, b[1][0])
                    if not czero(a10):
                        b[0][0] = csub(b[0][0], cmul(a10, b[0][1]))
                        b[1][0] = csub(b[1][0], cmul(a10, b[1][1]))
                    if in_diag == 1:
                        b[0][0] = cdiv(b[0][0], a00)
                        b[1][0] = cdiv(b[1][0], a00)
            else:
                conj = in_transa != 1
                if in_uplo == 0:
                    if in_diag == 1:
                        b[0][1] = cdiv(b[0][1], cconj(a11) if conj else a11)
                        b[1][1] = cdiv(b[1][1], cconj(a11) if conj else a11)
                    if not czero(cconj(a01) if conj else a01):
                        off = cconj(a01) if conj else a01
                        b[0][0] = csub(b[0][0], cmul(off, b[0][1]))
                        b[1][0] = csub(b[1][0], cmul(off, b[1][1]))
                    if not cone(alpha):
                        b[0][1] = cmul(alpha, b[0][1])
                        b[1][1] = cmul(alpha, b[1][1])
                    if in_diag == 1:
                        b[0][0] = cdiv(b[0][0], cconj(a00) if conj else a00)
                        b[1][0] = cdiv(b[1][0], cconj(a00) if conj else a00)
                    if not cone(alpha):
                        b[0][0] = cmul(alpha, b[0][0])
                        b[1][0] = cmul(alpha, b[1][0])
                else:
                    if in_diag == 1:
                        b[0][0] = cdiv(b[0][0], cconj(a00) if conj else a00)
                        b[1][0] = cdiv(b[1][0], cconj(a00) if conj else a00)
                    if not czero(cconj(a10) if conj else a10):
                        off = cconj(a10) if conj else a10
                        b[0][1] = csub(b[0][1], cmul(off, b[0][0]))
                        b[1][1] = csub(b[1][1], cmul(off, b[1][0]))
                    if not cone(alpha):
                        b[0][0] = cmul(alpha, b[0][0])
                        b[1][0] = cmul(alpha, b[1][0])
                    if in_diag == 1:
                        b[0][1] = cdiv(b[0][1], cconj(a11) if conj else a11)
                        b[1][1] = cdiv(b[1][1], cconj(a11) if conj else a11)
                    if not cone(alpha):
                        b[0][1] = cmul(alpha, b[0][1])
                        b[1][1] = cmul(alpha, b[1][1])

        out_b00_re = to_unsigned(b[0][0][0], 16)
        out_b00_im = to_unsigned(b[0][0][1], 16)
        out_b01_re = to_unsigned(b[0][1][0], 16)
        out_b01_im = to_unsigned(b[0][1][1], 16)
        out_b10_re = to_unsigned(b[1][0][0], 16)
        out_b10_im = to_unsigned(b[1][0][1], 16)
        out_b11_re = to_unsigned(b[1][1][0], 16)
        out_b11_im = to_unsigned(b[1][1][1], 16)

        self.out_b00_re = out_b00_re
        self.out_b00_im = out_b00_im
        self.out_b01_re = out_b01_re
        self.out_b01_im = out_b01_im
        self.out_b10_re = out_b10_re
        self.out_b10_im = out_b10_im
        self.out_b11_re = out_b11_re
        self.out_b11_im = out_b11_im

        return {
            "out_b00_re": out_b00_re,
            "out_b00_im": out_b00_im,
            "out_b01_re": out_b01_re,
            "out_b01_im": out_b01_im,
            "out_b10_re": out_b10_re,
            "out_b10_im": out_b10_im,
            "out_b11_re": out_b11_re,
            "out_b11_im": out_b11_im,
        }
