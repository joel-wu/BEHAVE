def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Correlate1DValid3:
    def __init__(self, signal_init=None):
        self.signal = [0] * 256 if signal_init is None else [to_unsigned(x, 16) for x in signal_init[:256]] + [0] * max(0, 256 - len(signal_init[:256]))
        self.reset()

    def reset(self):
        pass

    def process(self, in_base_addr, in_k0, in_k1, in_k2):
        base_addr = to_unsigned(in_base_addr, 8)
        k0 = to_signed(in_k0, 16)
        k1 = to_signed(in_k1, 16)
        k2 = to_signed(in_k2, 16)
        x0 = to_signed(self.signal[(base_addr + 0) & 0xFF], 16)
        x1 = to_signed(self.signal[(base_addr + 1) & 0xFF], 16)
        x2 = to_signed(self.signal[(base_addr + 2) & 0xFF], 16)
        corr = to_signed(x0 * k0, 32)
        corr = to_signed(corr + to_signed(x1 * k1, 32), 32)
        corr = to_signed(corr + to_signed(x2 * k2, 32), 32)
        return {"out_corr": to_unsigned(corr, 32)}