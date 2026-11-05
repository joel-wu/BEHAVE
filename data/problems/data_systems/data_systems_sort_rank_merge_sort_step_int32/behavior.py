class Argsort8Fp32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7):
        words = [in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7]
        keys = [0] * 8
        for i in range(8):
            word = words[i] & 0xffffffff
            magnitude = word & 0x7fffffff
            if magnitude > 0x7f800000:
                keys[i] = 0x100000000
            elif magnitude == 0:
                keys[i] = 0x80000000
            elif word & 0x80000000:
                keys[i] = (~word) & 0xffffffff
            else:
                keys[i] = word ^ 0x80000000
        order = [0, 1, 2, 3, 4, 5, 6, 7]
        for i in range(1, 8):
            for j in range(7, 0, -1):
                if j <= i:
                    if keys[order[j]] < keys[order[j-1]]:
                        saved = order[j-1]
                        order[j-1] = order[j]
                        order[j] = saved
        return {
            "out_p0": order[0], "out_p1": order[1],
            "out_p2": order[2], "out_p3": order[3],
            "out_p4": order[4], "out_p5": order[5],
            "out_p6": order[6], "out_p7": order[7],
        }
