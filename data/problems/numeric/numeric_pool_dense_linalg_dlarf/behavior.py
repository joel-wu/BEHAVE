def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def add32(a, b):
    return to_signed(a + b, 32)


def sub32(a, b):
    return to_signed(a - b, 32)


def mul32(a, b):
    return to_signed(a * b, 32)


class HouseholderReflector4x4Step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_side_left,
        in_tau,
        in_v0,
        in_v1,
        in_v2,
        in_v3,
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
        v = [
            to_signed(in_v0, 32),
            to_signed(in_v1, 32),
            to_signed(in_v2, 32),
            to_signed(in_v3, 32),
        ]
        c = [
            [to_signed(in_c00, 32), to_signed(in_c01, 32), to_signed(in_c02, 32), to_signed(in_c03, 32)],
            [to_signed(in_c10, 32), to_signed(in_c11, 32), to_signed(in_c12, 32), to_signed(in_c13, 32)],
            [to_signed(in_c20, 32), to_signed(in_c21, 32), to_signed(in_c22, 32), to_signed(in_c23, 32)],
            [to_signed(in_c30, 32), to_signed(in_c31, 32), to_signed(in_c32, 32), to_signed(in_c33, 32)],
        ]
        result = [
            [c[0][0], c[0][1], c[0][2], c[0][3]],
            [c[1][0], c[1][1], c[1][2], c[1][3]],
            [c[2][0], c[2][1], c[2][2], c[2][3]],
            [c[3][0], c[3][1], c[3][2], c[3][3]],
        ]

        lastv = 0
        if v[3] != 0:
            lastv = 4
        elif v[2] != 0:
            lastv = 3
        elif v[1] != 0:
            lastv = 2
        elif v[0] != 0:
            lastv = 1

        lastc = 0
        if tau != 0 and lastv != 0:
            if in_side_left:
                col_nz0 = 0
                col_nz1 = 0
                col_nz2 = 0
                col_nz3 = 0
                if lastv > 0:
                    if c[0][0] != 0:
                        col_nz0 = 1
                    if c[0][1] != 0:
                        col_nz1 = 1
                    if c[0][2] != 0:
                        col_nz2 = 1
                    if c[0][3] != 0:
                        col_nz3 = 1
                if lastv > 1:
                    if c[1][0] != 0:
                        col_nz0 = 1
                    if c[1][1] != 0:
                        col_nz1 = 1
                    if c[1][2] != 0:
                        col_nz2 = 1
                    if c[1][3] != 0:
                        col_nz3 = 1
                if lastv > 2:
                    if c[2][0] != 0:
                        col_nz0 = 1
                    if c[2][1] != 0:
                        col_nz1 = 1
                    if c[2][2] != 0:
                        col_nz2 = 1
                    if c[2][3] != 0:
                        col_nz3 = 1
                if lastv > 3:
                    if c[3][0] != 0:
                        col_nz0 = 1
                    if c[3][1] != 0:
                        col_nz1 = 1
                    if c[3][2] != 0:
                        col_nz2 = 1
                    if c[3][3] != 0:
                        col_nz3 = 1
                if col_nz3:
                    lastc = 4
                elif col_nz2:
                    lastc = 3
                elif col_nz1:
                    lastc = 2
                elif col_nz0:
                    lastc = 1
            else:
                row_nz0 = 0
                row_nz1 = 0
                row_nz2 = 0
                row_nz3 = 0
                if lastv > 0:
                    if c[0][0] != 0:
                        row_nz0 = 1
                    if c[1][0] != 0:
                        row_nz1 = 1
                    if c[2][0] != 0:
                        row_nz2 = 1
                    if c[3][0] != 0:
                        row_nz3 = 1
                if lastv > 1:
                    if c[0][1] != 0:
                        row_nz0 = 1
                    if c[1][1] != 0:
                        row_nz1 = 1
                    if c[2][1] != 0:
                        row_nz2 = 1
                    if c[3][1] != 0:
                        row_nz3 = 1
                if lastv > 2:
                    if c[0][2] != 0:
                        row_nz0 = 1
                    if c[1][2] != 0:
                        row_nz1 = 1
                    if c[2][2] != 0:
                        row_nz2 = 1
                    if c[3][2] != 0:
                        row_nz3 = 1
                if lastv > 3:
                    if c[0][3] != 0:
                        row_nz0 = 1
                    if c[1][3] != 0:
                        row_nz1 = 1
                    if c[2][3] != 0:
                        row_nz2 = 1
                    if c[3][3] != 0:
                        row_nz3 = 1
                if row_nz3:
                    lastc = 4
                elif row_nz2:
                    lastc = 3
                elif row_nz1:
                    lastc = 2
                elif row_nz0:
                    lastc = 1

        if tau != 0 and lastv != 0 and lastc != 0:
            if in_side_left:
                work = [0, 0, 0, 0]
                if lastc > 0:
                    work[0] = mul32(c[0][0], v[0])
                    if lastv > 1:
                        work[0] = add32(work[0], mul32(c[1][0], v[1]))
                    if lastv > 2:
                        work[0] = add32(work[0], mul32(c[2][0], v[2]))
                    if lastv > 3:
                        work[0] = add32(work[0], mul32(c[3][0], v[3]))
                if lastc > 1:
                    work[1] = mul32(c[0][1], v[0])
                    if lastv > 1:
                        work[1] = add32(work[1], mul32(c[1][1], v[1]))
                    if lastv > 2:
                        work[1] = add32(work[1], mul32(c[2][1], v[2]))
                    if lastv > 3:
                        work[1] = add32(work[1], mul32(c[3][1], v[3]))
                if lastc > 2:
                    work[2] = mul32(c[0][2], v[0])
                    if lastv > 1:
                        work[2] = add32(work[2], mul32(c[1][2], v[1]))
                    if lastv > 2:
                        work[2] = add32(work[2], mul32(c[2][2], v[2]))
                    if lastv > 3:
                        work[2] = add32(work[2], mul32(c[3][2], v[3]))
                if lastc > 3:
                    work[3] = mul32(c[0][3], v[0])
                    if lastv > 1:
                        work[3] = add32(work[3], mul32(c[1][3], v[1]))
                    if lastv > 2:
                        work[3] = add32(work[3], mul32(c[2][3], v[2]))
                    if lastv > 3:
                        work[3] = add32(work[3], mul32(c[3][3], v[3]))

                tau_v = [0, 0, 0, 0]
                if lastv > 0:
                    tau_v[0] = mul32(tau, v[0])
                if lastv > 1:
                    tau_v[1] = mul32(tau, v[1])
                if lastv > 2:
                    tau_v[2] = mul32(tau, v[2])
                if lastv > 3:
                    tau_v[3] = mul32(tau, v[3])

                if lastv > 0 and lastc > 0:
                    result[0][0] = sub32(c[0][0], mul32(tau_v[0], work[0]))
                if lastv > 0 and lastc > 1:
                    result[0][1] = sub32(c[0][1], mul32(tau_v[0], work[1]))
                if lastv > 0 and lastc > 2:
                    result[0][2] = sub32(c[0][2], mul32(tau_v[0], work[2]))
                if lastv > 0 and lastc > 3:
                    result[0][3] = sub32(c[0][3], mul32(tau_v[0], work[3]))

                if lastv > 1 and lastc > 0:
                    result[1][0] = sub32(c[1][0], mul32(tau_v[1], work[0]))
                if lastv > 1 and lastc > 1:
                    result[1][1] = sub32(c[1][1], mul32(tau_v[1], work[1]))
                if lastv > 1 and lastc > 2:
                    result[1][2] = sub32(c[1][2], mul32(tau_v[1], work[2]))
                if lastv > 1 and lastc > 3:
                    result[1][3] = sub32(c[1][3], mul32(tau_v[1], work[3]))

                if lastv > 2 and lastc > 0:
                    result[2][0] = sub32(c[2][0], mul32(tau_v[2], work[0]))
                if lastv > 2 and lastc > 1:
                    result[2][1] = sub32(c[2][1], mul32(tau_v[2], work[1]))
                if lastv > 2 and lastc > 2:
                    result[2][2] = sub32(c[2][2], mul32(tau_v[2], work[2]))
                if lastv > 2 and lastc > 3:
                    result[2][3] = sub32(c[2][3], mul32(tau_v[2], work[3]))

                if lastv > 3 and lastc > 0:
                    result[3][0] = sub32(c[3][0], mul32(tau_v[3], work[0]))
                if lastv > 3 and lastc > 1:
                    result[3][1] = sub32(c[3][1], mul32(tau_v[3], work[1]))
                if lastv > 3 and lastc > 2:
                    result[3][2] = sub32(c[3][2], mul32(tau_v[3], work[2]))
                if lastv > 3 and lastc > 3:
                    result[3][3] = sub32(c[3][3], mul32(tau_v[3], work[3]))
            else:
                work = [0, 0, 0, 0]
                if lastc > 0:
                    work[0] = mul32(c[0][0], v[0])
                    if lastv > 1:
                        work[0] = add32(work[0], mul32(c[0][1], v[1]))
                    if lastv > 2:
                        work[0] = add32(work[0], mul32(c[0][2], v[2]))
                    if lastv > 3:
                        work[0] = add32(work[0], mul32(c[0][3], v[3]))
                if lastc > 1:
                    work[1] = mul32(c[1][0], v[0])
                    if lastv > 1:
                        work[1] = add32(work[1], mul32(c[1][1], v[1]))
                    if lastv > 2:
                        work[1] = add32(work[1], mul32(c[1][2], v[2]))
                    if lastv > 3:
                        work[1] = add32(work[1], mul32(c[1][3], v[3]))
                if lastc > 2:
                    work[2] = mul32(c[2][0], v[0])
                    if lastv > 1:
                        work[2] = add32(work[2], mul32(c[2][1], v[1]))
                    if lastv > 2:
                        work[2] = add32(work[2], mul32(c[2][2], v[2]))
                    if lastv > 3:
                        work[2] = add32(work[2], mul32(c[2][3], v[3]))
                if lastc > 3:
                    work[3] = mul32(c[3][0], v[0])
                    if lastv > 1:
                        work[3] = add32(work[3], mul32(c[3][1], v[1]))
                    if lastv > 2:
                        work[3] = add32(work[3], mul32(c[3][2], v[2]))
                    if lastv > 3:
                        work[3] = add32(work[3], mul32(c[3][3], v[3]))

                tau_v = [0, 0, 0, 0]
                if lastv > 0:
                    tau_v[0] = mul32(tau, v[0])
                if lastv > 1:
                    tau_v[1] = mul32(tau, v[1])
                if lastv > 2:
                    tau_v[2] = mul32(tau, v[2])
                if lastv > 3:
                    tau_v[3] = mul32(tau, v[3])

                if lastc > 0 and lastv > 0:
                    result[0][0] = sub32(c[0][0], mul32(work[0], tau_v[0]))
                if lastc > 0 and lastv > 1:
                    result[0][1] = sub32(c[0][1], mul32(work[0], tau_v[1]))
                if lastc > 0 and lastv > 2:
                    result[0][2] = sub32(c[0][2], mul32(work[0], tau_v[2]))
                if lastc > 0 and lastv > 3:
                    result[0][3] = sub32(c[0][3], mul32(work[0], tau_v[3]))

                if lastc > 1 and lastv > 0:
                    result[1][0] = sub32(c[1][0], mul32(work[1], tau_v[0]))
                if lastc > 1 and lastv > 1:
                    result[1][1] = sub32(c[1][1], mul32(work[1], tau_v[1]))
                if lastc > 1 and lastv > 2:
                    result[1][2] = sub32(c[1][2], mul32(work[1], tau_v[2]))
                if lastc > 1 and lastv > 3:
                    result[1][3] = sub32(c[1][3], mul32(work[1], tau_v[3]))

                if lastc > 2 and lastv > 0:
                    result[2][0] = sub32(c[2][0], mul32(work[2], tau_v[0]))
                if lastc > 2 and lastv > 1:
                    result[2][1] = sub32(c[2][1], mul32(work[2], tau_v[1]))
                if lastc > 2 and lastv > 2:
                    result[2][2] = sub32(c[2][2], mul32(work[2], tau_v[2]))
                if lastc > 2 and lastv > 3:
                    result[2][3] = sub32(c[2][3], mul32(work[2], tau_v[3]))

                if lastc > 3 and lastv > 0:
                    result[3][0] = sub32(c[3][0], mul32(work[3], tau_v[0]))
                if lastc > 3 and lastv > 1:
                    result[3][1] = sub32(c[3][1], mul32(work[3], tau_v[1]))
                if lastc > 3 and lastv > 2:
                    result[3][2] = sub32(c[3][2], mul32(work[3], tau_v[2]))
                if lastc > 3 and lastv > 3:
                    result[3][3] = sub32(c[3][3], mul32(work[3], tau_v[3]))

        return {
            "out_c00": to_unsigned(result[0][0], 32),
            "out_c01": to_unsigned(result[0][1], 32),
            "out_c02": to_unsigned(result[0][2], 32),
            "out_c03": to_unsigned(result[0][3], 32),
            "out_c10": to_unsigned(result[1][0], 32),
            "out_c11": to_unsigned(result[1][1], 32),
            "out_c12": to_unsigned(result[1][2], 32),
            "out_c13": to_unsigned(result[1][3], 32),
            "out_c20": to_unsigned(result[2][0], 32),
            "out_c21": to_unsigned(result[2][1], 32),
            "out_c22": to_unsigned(result[2][2], 32),
            "out_c23": to_unsigned(result[2][3], 32),
            "out_c30": to_unsigned(result[3][0], 32),
            "out_c31": to_unsigned(result[3][1], 32),
            "out_c32": to_unsigned(result[3][2], 32),
            "out_c33": to_unsigned(result[3][3], 32),
        }