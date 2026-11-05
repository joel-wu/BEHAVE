def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SignalProcessingIspBayerRgbConversion:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_y, in_u, in_v, in_conv_std):
        y_off = in_y - 16
        u_off = in_u - 128
        v_off = in_v - 128

        if in_conv_std == 1:
            r_mul = 74 * y_off + 114 * v_off
            g_mul = 74 * y_off - 13 * u_off - 34 * v_off
            b_mul = 74 * y_off + 135 * u_off
        else:
            r_mul = 64 * y_off + 87 * v_off
            g_mul = 64 * y_off - 20 * u_off - 44 * v_off
            b_mul = 61 * y_off + 105 * u_off

        r_shift = r_mul >> 6
        g_shift = g_mul >> 6
        b_shift = b_mul >> 6

        out_r = 0 if r_shift < 0 else (255 if r_shift > 255 else r_shift)
        out_g = 0 if g_shift < 0 else (255 if g_shift > 255 else g_shift)
        out_b = 0 if b_shift < 0 else (255 if b_shift > 255 else b_shift)

        return {
            "out_r": to_unsigned(out_r, 8),
            "out_g": to_unsigned(out_g, 8),
            "out_b": to_unsigned(out_b, 8),
        }