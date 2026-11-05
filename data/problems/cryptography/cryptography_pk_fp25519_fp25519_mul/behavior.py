def to_unsigned(v, b):
    return v & ((1 << b) - 1)


P = (1 << 255) - 19


class FP25519Mul:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_y):
        x = to_unsigned(in_x, 256) % P
        y = to_unsigned(in_y, 256) % P
        z = (x * y) % P
        return {"out_z": to_unsigned(z, 256)}