def signed16(value):
    value &= 0xFFFF
    return value - 65536 if value >= 32768 else value


def isqrt_normalized(value):
    # A four-channel normalized magnitude is strictly below 256*sqrt(3).
    root = 0
    for bit in range(8, -1, -1):
        trial = root | (1 << bit)
        if trial * trial <= value:
            root = trial
    return root


def trunc256(value):
    return -((-value) // 256) if value < 0 else value // 256


class PoolNormChannelLayerNorm:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_weight, in_bias):
        x = [signed16(in_x >> (16*i)) for i in range(4)]
        weight = [signed16(in_weight >> (16*i)) for i in range(4)]
        bias = [signed16(in_bias >> (16*i)) for i in range(4)]
        total = sum(x)
        centered = [4*v - total for v in x]
        radicand = sum(v*v for v in centered) + 64
        packed = 0
        for i in range(4):
            numerator = centered[i] * 512
            magnitude = isqrt_normalized((numerator*numerator) // radicand)
            normalized = -magnitude if centered[i] < 0 else magnitude
            affine = trunc256(normalized*weight[i]) + bias[i]
            value = max(-32768, min(32767, affine))
            packed |= (value & 0xFFFF) << (16*i)
        return {"out_y": packed}
