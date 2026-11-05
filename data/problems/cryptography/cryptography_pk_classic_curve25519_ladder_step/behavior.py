P = (1 << 255) - 19
A24 = 121666


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Curve25519LadderStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x2, in_z2, in_x3, in_z3, in_xp):
        x2 = in_x2 % P
        z2 = in_z2 % P
        x3 = in_x3 % P
        z3 = in_z3 % P
        xp = in_xp % P

        d = (x3 - z3) % P
        b = (x2 - z2) % P
        a = (x2 + z2) % P
        c = (x3 + z3) % P
        da = (d * a) % P
        cb = (c * b) % P
        x5_pre = (da + cb) % P
        z5_pre = (da - cb) % P
        x5 = (x5_pre * x5_pre) % P
        t = (z5_pre * z5_pre) % P
        bb = (b * b) % P
        aa = (a * a) % P
        e = (aa - bb) % P
        z5 = (xp * t) % P
        u = (A24 * e) % P
        v = (bb + u) % P
        z4 = (e * v) % P
        x4 = (aa * bb) % P

        return {
            "out_x2": to_unsigned(x4, 256),
            "out_z2": to_unsigned(z4, 256),
            "out_x3": to_unsigned(x5, 256),
            "out_z3": to_unsigned(z5, 256),
        }