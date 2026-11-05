class Qam16Mapper:
    def __init__(self, N=4, IN_WIDTH=4, OUT_WIDTH=3):
        self.N = N
        self.IN_WIDTH = IN_WIDTH
        self.OUT_WIDTH = OUT_WIDTH

    def reset(self):
        pass

    def process(self, bits):
        I = 0
        Q = 0
        for pair in range(self.N // 2):
            s0 = (bits >> (8 * pair + 4)) & 15
            s1 = (bits >> (8 * pair)) & 15
            i0 = 2 * (s0 >> 2) - 3
            i1 = 2 * (s1 >> 2) - 3
            q0 = 2 * (s0 & 3) - 3
            q1 = 2 * (s1 & 3) - 3
            I |= ((i0 & 7) << (9 * pair + 6)) | ((((i0 + i1) >> 1) & 7) << (9 * pair + 3)) | ((i1 & 7) << (9 * pair))
            Q |= ((q0 & 7) << (9 * pair + 6)) | ((((q0 + q1) >> 1) & 7) << (9 * pair + 3)) | ((q1 & 7) << (9 * pair))
        return {"I": I, "Q": Q}
