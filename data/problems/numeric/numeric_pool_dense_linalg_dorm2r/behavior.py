def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class HouseholderQrApply4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_trans,
        in_a00, in_a10, in_a20, in_a30,
        in_a01, in_a11, in_a21, in_a31,
        in_a02, in_a12, in_a22, in_a32,
        in_a03, in_a13, in_a23, in_a33,
        in_tau0, in_tau1, in_tau2, in_tau3,
        in_c00, in_c10, in_c20, in_c30,
        in_c01, in_c11, in_c21, in_c31,
        in_c02, in_c12, in_c22, in_c32,
        in_c03, in_c13, in_c23, in_c33,
    ):
        r00, r10, r20, r30 = in_c00, in_c10, in_c20, in_c30
        r01, r11, r21, r31 = in_c01, in_c11, in_c21, in_c31
        r02, r12, r22, r32 = in_c02, in_c12, in_c22, in_c32
        r03, r13, r23, r33 = in_c03, in_c13, in_c23, in_c33

        if in_trans != 0:
            order = [0, 1, 2, 3]
        else:
            order = [3, 2, 1, 0]

        for i in order:
            if i == 0:
                dot = to_unsigned(r00 + to_unsigned(in_a10 * r10, 64) + to_unsigned(in_a20 * r20, 64) + to_unsigned(in_a30 * r30, 64), 64)
                w = to_unsigned(in_tau0 * dot, 64)
                r00 = to_unsigned(r00 - w, 64)
                r10 = to_unsigned(r10 - to_unsigned(in_a10 * w, 64), 64)
                r20 = to_unsigned(r20 - to_unsigned(in_a20 * w, 64), 64)
                r30 = to_unsigned(r30 - to_unsigned(in_a30 * w, 64), 64)

                dot = to_unsigned(r01 + to_unsigned(in_a10 * r11, 64) + to_unsigned(in_a20 * r21, 64) + to_unsigned(in_a30 * r31, 64), 64)
                w = to_unsigned(in_tau0 * dot, 64)
                r01 = to_unsigned(r01 - w, 64)
                r11 = to_unsigned(r11 - to_unsigned(in_a10 * w, 64), 64)
                r21 = to_unsigned(r21 - to_unsigned(in_a20 * w, 64), 64)
                r31 = to_unsigned(r31 - to_unsigned(in_a30 * w, 64), 64)

                dot = to_unsigned(r02 + to_unsigned(in_a10 * r12, 64) + to_unsigned(in_a20 * r22, 64) + to_unsigned(in_a30 * r32, 64), 64)
                w = to_unsigned(in_tau0 * dot, 64)
                r02 = to_unsigned(r02 - w, 64)
                r12 = to_unsigned(r12 - to_unsigned(in_a10 * w, 64), 64)
                r22 = to_unsigned(r22 - to_unsigned(in_a20 * w, 64), 64)
                r32 = to_unsigned(r32 - to_unsigned(in_a30 * w, 64), 64)

                dot = to_unsigned(r03 + to_unsigned(in_a10 * r13, 64) + to_unsigned(in_a20 * r23, 64) + to_unsigned(in_a30 * r33, 64), 64)
                w = to_unsigned(in_tau0 * dot, 64)
                r03 = to_unsigned(r03 - w, 64)
                r13 = to_unsigned(r13 - to_unsigned(in_a10 * w, 64), 64)
                r23 = to_unsigned(r23 - to_unsigned(in_a20 * w, 64), 64)
                r33 = to_unsigned(r33 - to_unsigned(in_a30 * w, 64), 64)
            elif i == 1:
                dot = to_unsigned(r10 + to_unsigned(in_a21 * r20, 64) + to_unsigned(in_a31 * r30, 64), 64)
                w = to_unsigned(in_tau1 * dot, 64)
                r10 = to_unsigned(r10 - w, 64)
                r20 = to_unsigned(r20 - to_unsigned(in_a21 * w, 64), 64)
                r30 = to_unsigned(r30 - to_unsigned(in_a31 * w, 64), 64)

                dot = to_unsigned(r11 + to_unsigned(in_a21 * r21, 64) + to_unsigned(in_a31 * r31, 64), 64)
                w = to_unsigned(in_tau1 * dot, 64)
                r11 = to_unsigned(r11 - w, 64)
                r21 = to_unsigned(r21 - to_unsigned(in_a21 * w, 64), 64)
                r31 = to_unsigned(r31 - to_unsigned(in_a31 * w, 64), 64)

                dot = to_unsigned(r12 + to_unsigned(in_a21 * r22, 64) + to_unsigned(in_a31 * r32, 64), 64)
                w = to_unsigned(in_tau1 * dot, 64)
                r12 = to_unsigned(r12 - w, 64)
                r22 = to_unsigned(r22 - to_unsigned(in_a21 * w, 64), 64)
                r32 = to_unsigned(r32 - to_unsigned(in_a31 * w, 64), 64)

                dot = to_unsigned(r13 + to_unsigned(in_a21 * r23, 64) + to_unsigned(in_a31 * r33, 64), 64)
                w = to_unsigned(in_tau1 * dot, 64)
                r13 = to_unsigned(r13 - w, 64)
                r23 = to_unsigned(r23 - to_unsigned(in_a21 * w, 64), 64)
                r33 = to_unsigned(r33 - to_unsigned(in_a31 * w, 64), 64)
            elif i == 2:
                dot = to_unsigned(r20 + to_unsigned(in_a32 * r30, 64), 64)
                w = to_unsigned(in_tau2 * dot, 64)
                r20 = to_unsigned(r20 - w, 64)
                r30 = to_unsigned(r30 - to_unsigned(in_a32 * w, 64), 64)

                dot = to_unsigned(r21 + to_unsigned(in_a32 * r31, 64), 64)
                w = to_unsigned(in_tau2 * dot, 64)
                r21 = to_unsigned(r21 - w, 64)
                r31 = to_unsigned(r31 - to_unsigned(in_a32 * w, 64), 64)

                dot = to_unsigned(r22 + to_unsigned(in_a32 * r32, 64), 64)
                w = to_unsigned(in_tau2 * dot, 64)
                r22 = to_unsigned(r22 - w, 64)
                r32 = to_unsigned(r32 - to_unsigned(in_a32 * w, 64), 64)

                dot = to_unsigned(r23 + to_unsigned(in_a32 * r33, 64), 64)
                w = to_unsigned(in_tau2 * dot, 64)
                r23 = to_unsigned(r23 - w, 64)
                r33 = to_unsigned(r33 - to_unsigned(in_a32 * w, 64), 64)
            else:
                dot = to_unsigned(r30, 64)
                w = to_unsigned(in_tau3 * dot, 64)
                r30 = to_unsigned(r30 - w, 64)

                dot = to_unsigned(r31, 64)
                w = to_unsigned(in_tau3 * dot, 64)
                r31 = to_unsigned(r31 - w, 64)

                dot = to_unsigned(r32, 64)
                w = to_unsigned(in_tau3 * dot, 64)
                r32 = to_unsigned(r32 - w, 64)

                dot = to_unsigned(r33, 64)
                w = to_unsigned(in_tau3 * dot, 64)
                r33 = to_unsigned(r33 - w, 64)

        return {
            "out_c00": to_unsigned(r00, 64),
            "out_c10": to_unsigned(r10, 64),
            "out_c20": to_unsigned(r20, 64),
            "out_c30": to_unsigned(r30, 64),
            "out_c01": to_unsigned(r01, 64),
            "out_c11": to_unsigned(r11, 64),
            "out_c21": to_unsigned(r21, 64),
            "out_c31": to_unsigned(r31, 64),
            "out_c02": to_unsigned(r02, 64),
            "out_c12": to_unsigned(r12, 64),
            "out_c22": to_unsigned(r22, 64),
            "out_c32": to_unsigned(r32, 64),
            "out_c03": to_unsigned(r03, 64),
            "out_c13": to_unsigned(r13, 64),
            "out_c23": to_unsigned(r23, 64),
            "out_c33": to_unsigned(r33, 64),
        }
