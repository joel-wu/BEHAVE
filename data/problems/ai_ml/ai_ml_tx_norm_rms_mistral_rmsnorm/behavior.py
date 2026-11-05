def signed16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x


def trunc15(x):
    return x >> 15 if x >= 0 else -((-x) >> 15)


def root(n):
    r = 0
    for bit in range(30, -1, -1):
        trial = r | (1 << bit)
        if trial * trial <= n:
            r = trial
    return r


def saturate16(x):
    return max(-32768, min(32767, x)) & 65535


class AiMlTxNormRmsMistralRmsnorm:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_hidden_0, in_hidden_1, in_hidden_2, in_hidden_3, in_hidden_4, in_hidden_5, in_hidden_6, in_hidden_7, in_weight_0, in_weight_1, in_weight_2, in_weight_3, in_weight_4, in_weight_5, in_weight_6, in_weight_7):
        x = [signed16(in_hidden_0), signed16(in_hidden_1), signed16(in_hidden_2), signed16(in_hidden_3), signed16(in_hidden_4), signed16(in_hidden_5), signed16(in_hidden_6), signed16(in_hidden_7)]
        w = [signed16(in_weight_0), signed16(in_weight_1), signed16(in_weight_2), signed16(in_weight_3), signed16(in_weight_4), signed16(in_weight_5), signed16(in_weight_6), signed16(in_weight_7)]
        total = 0
        for i in range(8):
            total += x[i] * x[i]
        denominator = total // 8 + 1074
        scale = root((1 << 60) // denominator)
        y = [0] * 8
        for i in range(8):
            normalized = trunc15(x[i] * scale)
            y[i] = saturate16(trunc15(normalized * w[i]))
        return {"out_hidden_0": y[0], "out_hidden_1": y[1], "out_hidden_2": y[2], "out_hidden_3": y[3], "out_hidden_4": y[4], "out_hidden_5": y[5], "out_hidden_6": y[6], "out_hidden_7": y[7]}
