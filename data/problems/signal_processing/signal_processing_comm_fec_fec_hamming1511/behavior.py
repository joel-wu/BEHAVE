def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def parity(x):
    p = 0
    for i in range(16):
        p ^= (x >> i) & 1
    return p


class SignalProcessingCommFecFecHamming1511:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_encode, in_symbol):
        if in_encode:
            data = in_symbol & 0x07ff
            p1 = parity(data & 0x06D5)
            p2 = parity(data & 0x05B3)
            p4 = parity(data & 0x038F)
            p8 = parity(data & 0x007F)
            enc = ((data & 0x007F) << 0) | ((data & 0x0380) << 1) | ((data & 0x0400) << 2) | (p1 << 14) | (p2 << 13) | (p4 << 11) | (p8 << 7)
            return {"out_symbol": to_unsigned(enc, 15)}

        enc = in_symbol & 0x7FFF
        s1 = parity(enc & 0x5555)
        s2 = parity(enc & 0x3333)
        s4 = parity(enc & 0x0F0F)
        s8 = parity(enc & 0x00FF)
        z = (s8 << 3) | (s4 << 2) | (s2 << 1) | s1
        if z != 0:
            enc ^= 1 << (15 - z)
        data = (enc & 0x007F) | ((enc & 0x0700) >> 1) | ((enc & 0x1000) >> 2)
        return {"out_symbol": to_unsigned(data, 11)}
