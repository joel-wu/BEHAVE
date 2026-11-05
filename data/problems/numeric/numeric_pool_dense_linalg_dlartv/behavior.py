def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class PlaneRotationVectorStep:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = [0] * 256
        self.y = [0] * 256
        self.c = [0] * 256
        self.s = [0] * 256

    def process(self, in_x_addr, in_y_addr, in_cs_addr):
        x_old = to_signed(self.x[in_x_addr], 32)
        y_old = to_signed(self.y[in_y_addr], 32)
        c_val = to_signed(self.c[in_cs_addr], 32)
        s_val = to_signed(self.s[in_cs_addr], 32)

        x_rot_full = c_val * x_old + s_val * y_old
        y_rot_full = c_val * y_old - s_val * x_old

        out_x_value = to_unsigned(x_rot_full, 32)
        out_y_value = to_unsigned(y_rot_full, 32)

        self.x[in_x_addr] = out_x_value
        self.y[in_y_addr] = out_y_value

        return {"out_x_value": out_x_value, "out_y_value": out_y_value}