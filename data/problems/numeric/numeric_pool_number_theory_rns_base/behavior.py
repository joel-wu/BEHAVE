class RnsExactBaseConvertQToT:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_y = 0

    def process(self, in_x0, in_x1, in_x2):
        x0 = in_x0 % 7
        x1 = in_x1 % 11
        x2 = in_x2 % 13

        a0 = (x0 * 5) % 7
        a1 = (x1 * 4) % 11
        a2 = (x2 * 12) % 13

        den = 7 * 11 * 13
        s_num = a0 * 11 * 13 + a1 * 7 * 13 + a2 * 7 * 11
        v = (s_num * 2 + den) // (2 * den)

        sum_mod_t = (a0 * 7 + a1 * 6 + a2 * 9) % 17
        corr = (v * 15) % 17
        y = (sum_mod_t - corr) % 17

        self.out_y = y
        return {"out_y": y & 0xFFFF}
