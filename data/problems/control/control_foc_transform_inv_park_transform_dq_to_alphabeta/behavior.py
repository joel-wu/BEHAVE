SIN_Q15_LUT = [0, 804, 1608, 2411, 3212, 4011, 4808, 5602, 6393, 7180, 7962, 8740, 9512, 10279, 11039, 11793, 12540, 13279, 14010, 14733, 15447, 16151, 16846, 17531, 18205, 18868, 19520, 20160, 20788, 21403, 22006, 22595, 23170, 23732, 24279, 24812, 25330, 25833, 26320, 26791, 27246, 27684, 28106, 28511, 28899, 29269, 29622, 29957, 30274, 30572, 30853, 31114, 31357, 31581, 31786, 31972, 32138, 32286, 32413, 32522, 32610, 32679, 32729, 32758, 32767]


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


def q15_mul(a, b):
    return sat16((a * b) >> 15)


class ControlFocTransformInvParkTransformDqToAlphabeta:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_d, in_q, in_theta):
        d = to_signed(in_d, 16)
        q = to_signed(in_q, 16)
        theta = to_unsigned(in_theta, 16)

        quadrant = (theta >> 14) & 0x3
        offset = (theta >> 8) & 0x3F

        if quadrant == 0:
            sin_theta = SIN_Q15_LUT[offset]
            cos_theta = SIN_Q15_LUT[64 - offset]
        elif quadrant == 1:
            sin_theta = SIN_Q15_LUT[64 - offset]
            cos_theta = -SIN_Q15_LUT[offset]
        elif quadrant == 2:
            sin_theta = -SIN_Q15_LUT[offset]
            cos_theta = -SIN_Q15_LUT[64 - offset]
        else:
            sin_theta = -SIN_Q15_LUT[64 - offset]
            cos_theta = SIN_Q15_LUT[offset]

        alpha_tmp = q15_mul(cos_theta, d) - q15_mul(sin_theta, q)
        beta_tmp = q15_mul(cos_theta, q) + q15_mul(sin_theta, d)

        out_alpha = sat16(alpha_tmp)
        out_beta = sat16(beta_tmp)

        return {
            "out_alpha": to_unsigned(out_alpha, 16),
            "out_beta": to_unsigned(out_beta, 16),
        }
