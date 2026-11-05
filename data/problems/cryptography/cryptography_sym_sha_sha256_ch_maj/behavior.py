def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SHA256ChMaj:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_y, in_z):
        x = to_unsigned(in_x, 32)
        y = to_unsigned(in_y, 32)
        z = to_unsigned(in_z, 32)
        out_ch = ((x & y) ^ (to_unsigned(~x, 32) & z))
        out_maj = ((x & y) ^ (x & z) ^ (y & z))
        return {"out_ch": to_unsigned(out_ch, 32), "out_maj": to_unsigned(out_maj, 32)}