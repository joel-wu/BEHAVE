def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc16(v):
    return to_signed(v, 16)


class BlockReflectorTriangularFactor4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_v00, in_v01, in_v02, in_v03,
        in_v10, in_v11, in_v12, in_v13,
        in_v20, in_v21, in_v22, in_v23,
        in_v30, in_v31, in_v32, in_v33,
        in_tau0, in_tau1, in_tau2, in_tau3,
    ):
        v10 = to_signed(in_v10, 16)
        v20 = to_signed(in_v20, 16)
        v21 = to_signed(in_v21, 16)
        v30 = to_signed(in_v30, 16)
        v31 = to_signed(in_v31, 16)
        v32 = to_signed(in_v32, 16)

        tau0 = to_signed(in_tau0, 16)
        tau1 = to_signed(in_tau1, 16)
        tau2 = to_signed(in_tau2, 16)
        tau3 = to_signed(in_tau3, 16)

        t11_00 = tau0
        t11_11 = tau1
        dot01 = trunc16(v10 + v20 * v21 + v30 * v31)
        t11_01 = trunc16(-tau0 * dot01 * tau1)

        t22_00 = tau2
        t22_11 = tau3
        t22_01 = trunc16(trunc16(-tau2 * v32) * tau3)

        s00 = v20
        s01 = v30
        s10 = v21
        s11 = v31

        r00 = trunc16(s00 + trunc16(s01 * v32))
        r01 = s01
        r10 = trunc16(s10 + trunc16(s11 * v32))
        r11 = s11

        l00 = trunc16(-(trunc16(t11_00 * r00) + trunc16(t11_01 * r10)))
        l01 = trunc16(-(trunc16(t11_00 * r01) + trunc16(t11_01 * r11)))
        l10 = trunc16(-(trunc16(t11_11 * r10)))
        l11 = trunc16(-(trunc16(t11_11 * r11)))

        t12_00 = trunc16(l00 * t22_00)
        t12_10 = trunc16(l10 * t22_00)
        t12_01 = trunc16(trunc16(l00 * t22_01) + trunc16(l01 * t22_11))
        t12_11 = trunc16(trunc16(l10 * t22_01) + trunc16(l11 * t22_11))

        return {
            "out_t00": to_unsigned(t11_00, 16),
            "out_t01": to_unsigned(t11_01, 16),
            "out_t02": to_unsigned(t12_00, 16),
            "out_t03": to_unsigned(t12_01, 16),
            "out_t10": 0,
            "out_t11": to_unsigned(t11_11, 16),
            "out_t12": to_unsigned(t12_10, 16),
            "out_t13": to_unsigned(t12_11, 16),
            "out_t20": 0,
            "out_t21": 0,
            "out_t22": to_unsigned(t22_00, 16),
            "out_t23": to_unsigned(t22_01, 16),
            "out_t30": 0,
            "out_t31": 0,
            "out_t32": 0,
            "out_t33": to_unsigned(t22_11, 16),
        }
