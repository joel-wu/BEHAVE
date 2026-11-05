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


class ai_ml_tx_norm_rms_qwen3_rmsnorm:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_w0, in_w1, in_w2, in_w3):
        x = [signed16(in_x0), signed16(in_x1), signed16(in_x2), signed16(in_x3)]
        w = [signed16(in_w0), signed16(in_w1), signed16(in_w2), signed16(in_w3)]
        total = 0
        for i in range(4):
            total += x[i] * x[i]
        denominator = total // 4 + 1074
        scale = root((1 << 60) // denominator)
        y = [0] * 4
        for i in range(4):
            normalized = trunc15(x[i] * scale)
            y[i] = saturate16(trunc15(normalized * w[i]))
        return {"out_y0": y[0], "out_y1": y[1], "out_y2": y[2], "out_y3": y[3]}
