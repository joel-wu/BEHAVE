def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap32(v):
    return to_signed(v, 32)


class SparseFirQ15Block4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0] * 11
        self.stateIndex = 0

    def process(self, in_x0, in_x1, in_x2, in_x3):
        x_in = [
            to_signed(in_x0, 16),
            to_signed(in_x1, 16),
            to_signed(in_x2, 16),
            to_signed(in_x3, 16),
        ]

        state = list(self.state)
        state_index = self.stateIndex

        state[state_index] = x_in[0]
        state[(state_index + 1) % 11] = x_in[1]
        state[(state_index + 2) % 11] = x_in[2]
        state[(state_index + 3) % 11] = x_in[3]

        new_stateIndex = (state_index + 4) % 11

        delays = [0, 2, 5, 7]
        coeffs = [0x2000, to_signed(0xE000, 16), 0x1000, 0x0800]

        read_base = [((new_stateIndex - 4 - d) % 11) for d in delays]

        out = []
        for k in range(4):
            acc = wrap32(state[(read_base[0] + k) % 11] * coeffs[0])
            acc = wrap32(acc + state[(read_base[1] + k) % 11] * coeffs[1])
            acc = wrap32(acc + state[(read_base[2] + k) % 11] * coeffs[2])
            acc = wrap32(acc + state[(read_base[3] + k) % 11] * coeffs[3])
            tmp = acc >> 15
            if tmp > 32767:
                tmp = 32767
            elif tmp < -32768:
                tmp = -32768
            out.append(to_unsigned(tmp, 16))

        self.state = state
        self.stateIndex = new_stateIndex

        return {
            "out_y0": out[0],
            "out_y1": out[1],
            "out_y2": out[2],
            "out_y3": out[3],
        }