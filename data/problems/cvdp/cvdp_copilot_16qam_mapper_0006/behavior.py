def signed3(value):
    value &= 7
    return value - 8 if value & 4 else value


def demap(value):
    return 1 if value == -1 else 2 if value == 1 else 3 if value == 3 else 0


class Qam16Demapper:
    def __init__(self, N=4, IN_WIDTH=3, OUT_WIDTH=4, ERROR_THRESHOLD=1):
        self.N = N
        self.IN_WIDTH = IN_WIDTH
        self.OUT_WIDTH = OUT_WIDTH
        self.ERROR_THRESHOLD = ERROR_THRESHOLD

    def reset(self):
        pass

    def process(self, I, Q):
        bits = 0
        error_flag = 0
        for pair in range(self.N // 2):
            i0 = signed3(I >> (9 * pair + 6))
            im = signed3(I >> (9 * pair + 3))
            i1 = signed3(I >> (9 * pair))
            q0 = signed3(Q >> (9 * pair + 6))
            qm = signed3(Q >> (9 * pair + 3))
            q1 = signed3(Q >> (9 * pair))
            bits |= ((demap(i0) << 2) | demap(q0)) << (8 * pair + 4)
            bits |= ((demap(i1) << 2) | demap(q1)) << (8 * pair)
            di = im - ((i0 + i1) >> 1)
            dq = qm - ((q0 + q1) >> 1)
            if di < -self.ERROR_THRESHOLD or di > self.ERROR_THRESHOLD or dq < -self.ERROR_THRESHOLD or dq > self.ERROR_THRESHOLD:
                error_flag = 1
        return {"bits": bits, "error_flag": error_flag}
