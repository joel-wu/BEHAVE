def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyHeNttHexlHarveyInvButterfly:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x_op, in_y_op, in_w, in_w_precon, in_modulus):
        x = to_unsigned(in_x_op, 64)
        y = to_unsigned(in_y_op, 64)
        w = to_unsigned(in_w, 64)
        w_precon = to_unsigned(in_w_precon, 64)
        q = to_unsigned(in_modulus, 64)

        tx = to_unsigned(x + y, 64)
        two_q = to_unsigned(q << 1, 64)
        x_red = tx - two_q if tx >= two_q else tx

        two_q = to_unsigned(q << 1, 64)
        diff = to_unsigned(x + two_q - y, 64)

        prod = diff * w
        precon_prod = diff * w_precon
        q_hat = (precon_prod >> 64) & ((1 << 64) - 1)
        candidate = to_unsigned(prod - q_hat * q, 64)

        y_red = candidate

        return {
            "out_x_r": to_unsigned(x_red, 64),
            "out_y_r": to_unsigned(y_red, 64),
        }
