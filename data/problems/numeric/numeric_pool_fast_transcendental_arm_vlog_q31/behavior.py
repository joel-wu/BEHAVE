def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Q31NaturalLog:
    def __init__(self):
        self.reset()

    def reset(self):
        self._idle = 0

    def process(self, in_src):
        src = to_unsigned(in_src, 32)
        c = 32
        for i in range(32):
            if ((src >> (31 - i)) & 1) != 0:
                c = i
                break
        if c == 0:
            x = src >> 1
        else:
            x = to_unsigned(src << (c - 1), 32)
        normalization = c
        inc = 0x02000000
        y = 0
        for _ in range(31):
            x = ((x * x) >> 30) & 0xFFFFFFFF
            if x >= 0x80000000:
                y = (y + inc) & 0xFFFFFFFF
                x >>= 1
            inc >>= 1
        tmp = y - (normalization << 26)
        result = (tmp * 0x58B90BFB) >> 31
        return {"out_dst": to_unsigned(result, 32)}