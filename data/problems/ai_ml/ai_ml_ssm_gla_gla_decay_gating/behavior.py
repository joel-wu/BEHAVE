def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


LOG1P_EXP_TABLE = (45426, 43410, 41458, 39570, 37745, 35983, 34283, 32646, 31069, 29553, 28095, 26696, 25354, 24068, 22836, 21657, 20530, 19453, 18425, 17445, 16510, 15620, 14773, 13966, 13200, 12471, 11780, 11123, 10500, 9910, 9350, 8820, 8318, 7843, 7394, 6969, 6567, 6187, 5829, 5490, 5170, 4868, 4583, 4315, 4061, 3822, 3597, 3384, 3184, 2996, 2818, 2651, 2493, 2345, 2205, 2074, 1950, 1833, 1724, 1620, 1523, 1432, 1346, 1265, 1189, 1118, 1051, 988, 928, 872, 820, 770, 724, 680, 639, 601, 565, 530, 498, 468, 440, 414, 389, 365, 343, 322, 303, 284, 267, 251, 236, 222, 208, 196, 184, 173, 162, 152, 143, 135, 126, 119, 112, 105, 98, 92, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22)


class GLADecayGating:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_gk_logit, in_clamp_min, in_clamp_en):
        x = to_signed(in_gk_logit, 16)
        a = -x if x < 0 else x
        correction = 0
        if a < 2048:
            j = a // 16
            r = a % 16
            correction = (16 - r) * LOG1P_EXP_TABLE[j] + r * LOG1P_EXP_TABLE[j + 1]
        negative = -x if x < 0 else 0
        magnitude = negative * 4096 + correction
        q = magnitude // 65536
        remainder = magnitude % 65536
        if remainder > 32768 or (remainder == 32768 and (q & 1) != 0):
            q += 1
        result = -q
        clamp = to_signed(in_clamp_min, 16)
        if (in_clamp_en & 1) != 0 and result < clamp:
            result = clamp
        return {"out_gk": result & 65535}
