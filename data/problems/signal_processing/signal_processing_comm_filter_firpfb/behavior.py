def signed16(value):
    value &= 65535
    return value - 65536 if value & 32768 else value


class FirPfb4Phase4Tap:
    def __init__(self, H_FLAT=0x0003000200010001ffff0002fffe00030002ffff0001fffe0004000300020001, SCALE=1):
        self.h = [signed16(H_FLAT >> (16*k)) for k in range(16)]
        self.scale = signed16(SCALE)
        self.reset()

    def reset(self):
        self.delay = [0, 0, 0]

    def process(self, in_x, in_i):
        x = signed16(in_x)
        i = in_i & 3
        total = self.h[i]*x + self.h[i+4]*self.delay[0] + self.h[i+8]*self.delay[1] + self.h[i+12]*self.delay[2]
        self.delay = [x, self.delay[0], self.delay[1]]
        return {"out_y": (total*self.scale) & ((1 << 64)-1)}
