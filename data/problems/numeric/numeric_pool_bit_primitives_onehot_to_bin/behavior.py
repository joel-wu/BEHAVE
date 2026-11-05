def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class OnehotToBin:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_onehot):
        onehot = to_unsigned(in_onehot, 16)
        out_bin = 0
        out_bin |= (((onehot >> 1) & 1) | ((onehot >> 3) & 1) | ((onehot >> 5) & 1) | ((onehot >> 7) & 1) |
                    ((onehot >> 9) & 1) | ((onehot >> 11) & 1) | ((onehot >> 13) & 1) | ((onehot >> 15) & 1)) << 0
        out_bin |= (((onehot >> 2) & 1) | ((onehot >> 3) & 1) | ((onehot >> 6) & 1) | ((onehot >> 7) & 1) |
                    ((onehot >> 10) & 1) | ((onehot >> 11) & 1) | ((onehot >> 14) & 1) | ((onehot >> 15) & 1)) << 1
        out_bin |= (((onehot >> 4) & 1) | ((onehot >> 5) & 1) | ((onehot >> 6) & 1) | ((onehot >> 7) & 1) |
                    ((onehot >> 12) & 1) | ((onehot >> 13) & 1) | ((onehot >> 14) & 1) | ((onehot >> 15) & 1)) << 2
        out_bin |= (((onehot >> 8) & 1) | ((onehot >> 9) & 1) | ((onehot >> 10) & 1) | ((onehot >> 11) & 1) |
                    ((onehot >> 12) & 1) | ((onehot >> 13) & 1) | ((onehot >> 14) & 1) | ((onehot >> 15) & 1)) << 3
        return {"out_bin": to_unsigned(out_bin, 4)}