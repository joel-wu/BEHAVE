def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingCommModemModemAsk:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def gray_encode(self, n):
        n = to_unsigned(n, 3)
        return to_unsigned(n ^ (n >> 1), 3)

    def gray_decode(self, g):
        g = to_unsigned(g, 3)
        b2 = (g >> 2) & 1
        b1 = b2 ^ ((g >> 1) & 1)
        b0 = b1 ^ (g & 1)
        return (b2 << 2) | (b1 << 1) | b0

    def amplitude(self, s):
        return to_signed((s << 1) - 7, 16)

    def process(self, in_demod, in_symbol, in_x):
        in_symbol_u = to_unsigned(in_symbol, 3)
        in_x_s = to_signed(in_x, 16)

        if to_unsigned(in_demod, 1) == 0:
            s = self.gray_decode(in_symbol_u)
            y = self.amplitude(s)
            return {
                "out_symbol": in_symbol_u,
                "out_y": to_unsigned(y, 16),
                "out_x_hat": to_unsigned(y, 16),
                "out_r": 0,
            }

        best_s = 0
        best_d = in_x_s - self.amplitude(0)
        if best_d < 0:
            best_d = -best_d

        for s in range(1, 8):
            d = in_x_s - self.amplitude(s)
            if d < 0:
                d = -d
            if d < best_d:
                best_d = d
                best_s = s

        x_hat = self.amplitude(best_s)
        return {
            "out_symbol": to_unsigned(self.gray_encode(best_s), 3),
            "out_y": to_unsigned(x_hat, 16),
            "out_x_hat": to_unsigned(x_hat, 16),
            "out_r": to_unsigned(in_x_s, 16),
        }