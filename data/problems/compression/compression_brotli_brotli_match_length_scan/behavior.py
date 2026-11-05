class CompressionBrotliBrotliMatchLengthScan:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_s1, in_s2, in_limit):
        limit_eff = in_limit if in_limit < 32 else 32
        matched = 0
        while matched + 4 <= limit_eff:
            w1 = (in_s1 >> (8 * matched)) & 0xFFFFFFFF
            w2 = (in_s2 >> (8 * matched)) & 0xFFFFFFFF
            if w1 != w2:
                break
            matched += 4
        while matched < limit_eff:
            b1 = (in_s1 >> (8 * matched)) & 0xFF
            b2 = (in_s2 >> (8 * matched)) & 0xFF
            if b1 != b2:
                break
            matched += 1
        return {"out_matched": matched & 0x3F}