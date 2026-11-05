class Q15CorrelationStream:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sample = [0] * 256

    def process(self, in_srcA_base, in_srcA_len_m1, in_srcB_base, in_srcB_len_m1):
        na = (in_srcA_len_m1 & 7) + 1
        nb = (in_srcB_len_m1 & 7) + 1
        a = [self.sample[(in_srcA_base + i) & 255] for i in range(na)]
        b = [self.sample[(in_srcB_base + i) & 255] for i in range(nb)]
        outputs = []
        for k in range(na + nb - 1):
            total = 0
            for i in range(na):
                j = i - k + nb - 1
                if 0 <= j < nb:
                    x = (a[i] + 32768) % 65536 - 32768
                    y = (b[j] + 32768) % 65536 - 32768
                    total += x * y
            value = max(-32768, min(32767, total >> 15))
            outputs.append({"out_data": value & 65535})
        return outputs
