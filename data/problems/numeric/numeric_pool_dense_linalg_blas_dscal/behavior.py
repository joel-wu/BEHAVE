def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class VectorScale16x32Burst:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = [0] * 65536

    def process(self, in_alpha, in_base_addr):
        alpha = to_signed(in_alpha, 32)
        base_addr = to_unsigned(in_base_addr, 16)

        if alpha == 1:
            return {"out_count": 0}

        for k in range(16):
            addr = to_unsigned(base_addr + k, 16)
            x_k = to_signed(self.x[addr], 32)
            self.x[addr] = to_unsigned(alpha * x_k, 32)

        return {"out_count": 16}