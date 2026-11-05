def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def parity8(x):
    x &= 0xFF
    p = 0
    for i in range(8):
        p ^= (x >> i) & 1
    return p


def parity12(x):
    x &= 0xFFF
    p = 0
    for i in range(12):
        p ^= (x >> i) & 1
    return p


class SignalProcessingCommFecFecHamming128:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_encode, in_symbol):
        if in_encode:
            d = in_symbol & 0xFF
            p1 = parity8(d & 0xDA)
            p2 = parity8(d & 0xB6)
            p4 = parity8(d & 0x71)
            p8 = parity8(d & 0x0F)
            out_symbol = ((d & 0x00F) << 0) | ((d & 0x070) << 1) | ((d & 0x080) << 2) | (p1 << 11) | (p2 << 10) | (p4 << 8) | (p8 << 4)
            return {"out_symbol": to_unsigned(out_symbol, 12)}
        c = in_symbol & 0xFFF
        s1 = parity12(c & 0xAAA)
        s2 = parity12(c & 0x666)
        s4 = parity12(c & 0x1E1)
        s8 = parity12(c & 0x01F)
        z = (s8 << 3) | (s4 << 2) | (s2 << 1) | s1
        if z != 0 and z <= 12:
            c ^= 1 << (12 - z)
        d = ((c & 0x00F) >> 0) | ((c & 0x0E0) >> 1) | ((c & 0x200) >> 2)
        return {"out_symbol": to_unsigned(d, 12)}