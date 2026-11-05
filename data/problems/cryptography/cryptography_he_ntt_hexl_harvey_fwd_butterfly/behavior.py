def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyHeNttHexlHarveyFwdButterfly:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x_op, in_y_op, in_w, in_w_precon, in_modulus):
        q = to_unsigned(in_modulus, 64)
        twice_modulus = to_unsigned(q << 1, 64)

        x_op = to_unsigned(in_x_op, 64)
        y_op = to_unsigned(in_y_op, 64)
        w = to_unsigned(in_w, 64)
        w_precon = to_unsigned(in_w_precon, 64)

        if x_op >= twice_modulus:
            tx = x_op - twice_modulus
        else:
            tx = x_op

        product = to_unsigned(y_op * w, 128)
        q_hat = (to_unsigned(y_op * w_precon, 128) >> 64) & ((1 << 64) - 1)
        candidate = to_unsigned(product - q_hat * q, 64)

        t = candidate

        out_x_r = to_unsigned(tx + t, 64)
        out_y_r = to_unsigned(tx + twice_modulus - t, 64)

        return {"out_x_r": out_x_r, "out_y_r": out_y_r}
