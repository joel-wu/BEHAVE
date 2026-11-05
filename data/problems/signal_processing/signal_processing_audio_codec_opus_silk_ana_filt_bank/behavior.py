def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def rshift_round_11(v):
    return ((v >> 10) + 1) >> 1


class SignalProcessingAudioCodecOpusSilkAnaFiltBank:
    def __init__(self):
        self.reset()

    def reset(self):
        self.S = [0, 0]

    def process(self, in_s0, in_s1):
        s0 = self.S[0]
        s1 = self.S[1]

        in32_0 = to_signed(in_s0, 16) << 10
        y0 = in32_0 - s0
        x0 = y0 + ((y0 * -24290) >> 16)
        out_1 = s0 + x0
        new_s0 = in32_0 + x0

        in32_1 = to_signed(in_s1, 16) << 10
        y1 = in32_1 - s1
        x1 = (y1 * 10788) >> 16
        out_2 = s1 + x1
        new_s1 = in32_1 + x1

        sum_l = out_1 + out_2
        sum_h = out_2 - out_1

        out_l = sat16(rshift_round_11(sum_l))
        out_h = sat16(rshift_round_11(sum_h))

        self.S[0] = new_s0
        self.S[1] = new_s1

        return {
            "out_outL": to_unsigned(out_l, 16),
            "out_outH": to_unsigned(out_h, 16),
        }
