EXP = [4294967296, 1580030169, 581260615, 213833830, 78665070, 28939262, 10646160, 3916503, 1440801, 530041, 194991, 71733, 26389, 9708, 3571, 1314, 483, 178, 65, 24, 9, 3, 1]


def signed16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x


class AiMlSampleFilterSampleToppMask:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_top_p, in_logits):
        p = in_top_p & 131071
        if p > 65536:
            p = 65536
        logits = [signed16(in_logits >> (16*i)) for i in range(8)]
        high = logits[0]
        for i in range(1,8):
            if logits[i] > high:
                high = logits[i]
        weights = [0]*8
        den = 0
        for i in range(8):
            d = high-logits[i]
            if d < 23:
                weights[i] = EXP[d]
            den += weights[i]
        mask = 0
        for i in range(8):
            cumulative = 0
            count = 0
            for j in range(8):
                if logits[j] < logits[i] or (logits[j] == logits[i] and j <= i):
                    cumulative += weights[j]
                    count += 1
            if count == 8 or cumulative*65536 > (65536-p)*den:
                mask |= 1 << i
        return {'out_mask': mask}
