def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def clamp_1_4(v):
    if v < 1:
        return 1
    if v > 4:
        return 4
    return v


def floor_div_pos_den(num, den):
    return num // den


class AiMlVisionPoolNormAdaptiveAvgPool2d:
    def __init__(self):
        self.reset()

    def reset(self):
        self._idle = 0

    def process(self, in_h, in_w, out_h, out_w, in_data):
        h_in = clamp_1_4(in_h)
        w_in = clamp_1_4(in_w)
        h_out = clamp_1_4(out_h)
        w_out = clamp_1_4(out_w)

        x = [to_signed((in_data >> (16 * i)) & 0xFFFF, 16) for i in range(16)]
        out_data = 0

        for orow in range(4):
            for ocol in range(4):
                y = 0
                if orow < h_out and ocol < w_out:
                    h_start = (orow * h_in) // h_out
                    h_end = (((orow + 1) * h_in) + h_out - 1) // h_out
                    w_start = (ocol * w_in) // w_out
                    w_end = (((ocol + 1) * w_in) + w_out - 1) // w_out

                    total = 0
                    for r in range(4):
                        for c in range(4):
                            if h_start <= r < h_end and w_start <= c < w_end:
                                total += x[4 * r + c]

                    count = (h_end - h_start) * (w_end - w_start)
                    y = floor_div_pos_den(total, count)

                out_data |= to_unsigned(y, 16) << (16 * (4 * orow + ocol))

        return {"out_data": to_unsigned(out_data, 256)}
