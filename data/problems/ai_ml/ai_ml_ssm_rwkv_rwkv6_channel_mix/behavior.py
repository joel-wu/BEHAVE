def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlSsmRwkvRwkv6ChannelMix:
    def __init__(self):
        self.reset()

    def reset(self):
        self.prev_x = 0

    def process(self, in_x, in_mu):
        x = to_signed(in_x, 32)
        mu = to_signed(in_mu, 32)
        prev_x = self.prev_x
        delta = prev_x - x
        product = delta * mu
        mixed = product // 65536 if product >= 0 else -((-product) // 65536)
        y = to_signed(x + mixed, 32)
        self.prev_x = x
        return {"out_y": to_unsigned(y, 32)}
