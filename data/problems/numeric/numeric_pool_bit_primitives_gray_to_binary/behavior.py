def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class GrayToBinary8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a):
        in_a = to_unsigned(in_a, 8)
        out_z = 0
        parity = 0
        for i in range(7, -1, -1):
            parity ^= (in_a >> i) & 1
            out_z |= parity << i
        return {"out_z": to_unsigned(out_z, 8)}