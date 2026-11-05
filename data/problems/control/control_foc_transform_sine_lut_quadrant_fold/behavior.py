LUT = [
    0, 804, 1608, 2411, 3212, 4011, 4808, 5602, 6393, 7180, 7962, 8740,
    9512, 10279, 11039, 11793, 12540, 13279, 14010, 14733, 15447, 16151,
    16846, 17531, 18205, 18868, 19520, 20160, 20788, 21403, 22006, 22595,
    23170, 23732, 24279, 24812, 25330, 25833, 26320, 26791, 27246, 27684,
    28106, 28511, 28899, 29269, 29622, 29957, 30274, 30572, 30853, 31114,
    31357, 31581, 31786, 31972, 32138, 32286, 32413, 32522, 32610, 32679,
    32729, 32758, 32768,
]


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ControlFocTransformSineLutQuadrantFold:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def _sine_lut_fold(self, p):
        p &= 0xFFFF
        quadrant = (p >> 14) & 0x3
        offset = p & 0x3FFF
        index = (offset >> 8) & 0x3F
        frac = offset & 0xFF
        if quadrant == 0:
            t1 = LUT[index]
            t2 = LUT[index + 1]
        elif quadrant == 1:
            t1 = LUT[64 - index]
            t2 = LUT[63 - index]
        elif quadrant == 2:
            t1 = -LUT[index]
            t2 = -LUT[index + 1]
        else:
            t1 = -LUT[64 - index]
            t2 = -LUT[63 - index]
        sin_raw = t1 + (((t2 - t1) * frac) >> 8)
        return to_unsigned(sin_raw, 17)

    def process(self, in_angle):
        angle = in_angle & 0xFFFF
        out_sin = self._sine_lut_fold(angle)
        out_cos = self._sine_lut_fold((angle + 0x4000) & 0xFFFF)
        return {"out_sin": out_sin, "out_cos": out_cos}
