class CtcGreedy8x4Int16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_scores, in_length, in_blank):
        count = 0
        tokens = 0
        times = 0
        previous = 0
        length = in_length & 15
        blank = in_blank & 3
        for t in range(8):
            if t < length:
                best = (in_scores >> (64*t)) & 65535
                if best >= 32768:
                    best -= 65536
                label = 0
                for k in range(1,4):
                    score = (in_scores >> (16*(4*t+k))) & 65535
                    if score >= 32768:
                        score -= 65536
                    if score > best:
                        best = score
                        label = k
                if (t == 0 or label != previous) and label != blank:
                    tokens |= label << (2*count)
                    times |= t << (3*count)
                    count += 1
                previous = label
        return {"out_count": count, "out_tokens": tokens, "out_times": times}
