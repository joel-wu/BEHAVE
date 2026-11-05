SIN_Q15_TABLE = [0, 804, 1608, 2411, 3212, 4011, 4808, 5602, 6393, 7180, 7962, 8740, 9512, 10279, 11039, 11793, 12540, 13279, 14010, 14733, 15447, 16151, 16846, 17531, 18205, 18868, 19520, 20160, 20788, 21403, 22006, 22595, 23170, 23732, 24279, 24812, 25330, 25833, 26320, 26791, 27246, 27684, 28106, 28511, 28899, 29269, 29622, 29957, 30274, 30572, 30853, 31114, 31357, 31581, 31786, 31972, 32138, 32286, 32413, 32522, 32610, 32679, 32729, 32758, 32767]


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


class ControlFocTransformParkTransformAlphabetaToDq:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_alpha, in_beta, in_theta):
        alpha = to_signed(in_alpha, 16)
        beta = to_signed(in_beta, 16)
        theta_u = to_unsigned(in_theta, 16)
        quad = (theta_u >> 14) & 0x3
        frac = theta_u & 0x3FFF
        idx = (frac >> 8) & 0x3F

        if quad == 0:
            sin_mag = SIN_Q15_TABLE[idx]
            cos_mag = SIN_Q15_TABLE[64 - idx]
            sin_v = sin_mag
            cos_v = cos_mag
        elif quad == 1:
            sin_mag = SIN_Q15_TABLE[64 - idx]
            cos_mag = SIN_Q15_TABLE[idx]
            sin_v = sin_mag
            cos_v = -cos_mag
        elif quad == 2:
            sin_mag = SIN_Q15_TABLE[idx]
            cos_mag = SIN_Q15_TABLE[64 - idx]
            sin_v = -sin_mag
            cos_v = -cos_mag
        else:
            sin_mag = SIN_Q15_TABLE[64 - idx]
            cos_mag = SIN_Q15_TABLE[idx]
            sin_v = -sin_mag
            cos_v = cos_mag

        t_ca = (cos_v * alpha) >> 15
        t_sb = (sin_v * beta) >> 15
        t_cb = (cos_v * beta) >> 15
        t_sa = (sin_v * alpha) >> 15

        d_sat = sat16(t_ca + t_sb)
        q_sat = sat16(t_cb - t_sa)

        return {
            "out_d": to_unsigned(d_sat, 16),
            "out_q": to_unsigned(q_sat, 16),
        }
