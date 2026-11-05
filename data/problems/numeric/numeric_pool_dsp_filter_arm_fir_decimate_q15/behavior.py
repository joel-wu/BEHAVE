def to_signed(value, width=16):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def sat16(value):
    return max(-32768, min(32767, value))


class Q15FirDecimator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0]*8
        self.phase = 0
        self.saved = 0

    def process(self, in_sample):
        self.state = self.state[1:] + [to_signed(in_sample)]
        if self.phase == 0:
            self.saved = sat16(sum(sample*4096 for sample in self.state) >> 15)
        self.phase ^= 1
        if self.phase == 0:
            return {"out_sample": self.saved & 65535}
        return None
