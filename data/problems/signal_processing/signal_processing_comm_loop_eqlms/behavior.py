def signed32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def trunc_div(value, denominator):
    q = abs(value) // denominator
    return -q if value < 0 else q


class SignalProcessingCommLoopEqlms:
    def __init__(self):
        self.reset()

    def reset(self):
        self.w0 = [0, 0, 0, 0, 1, 0, 0, 0]
        self.buffer = [0] * 8
        self.count = 0

    def process(self, in_x, in_d):
        samples = [signed32(in_x)] + self.buffer[:7]
        energy = 0
        result = 0
        for i in range(8):
            energy += samples[i] * samples[i]
            result += self.w0[i] * samples[i]
        y = signed32(result)
        count = min(self.count + 1, 8)
        weights = [0] * 8
        for i in range(8):
            delta = 0
            if count == 8 and energy != 0:
                delta = trunc_div((signed32(in_d) - y) * samples[i], 2 * energy)
            weights[i] = signed32(self.w0[i] + delta)
        self.w0 = weights
        self.buffer = samples
        self.count = count
        return {"out_y": y & 0xFFFFFFFF, "out_updated": int(count == 8)}
