def signed16(value):
    word = value & 65535
    return word - 65536 if word >= 32768 else word


def qmul(left, right):
    return signed16((signed16(left) * signed16(right)) >> 8)


class Zamba2SSMDecode:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0] * 12

    def process(self, in_x, in_decay, in_dB, in_C, in_D):
        output = 0
        packed_state = 0
        for h in range(2):
            decay = signed16(in_decay >> (16 * h))
            skip = signed16(in_D >> (16 * h))
            for d in range(2):
                x = signed16(in_x >> (16 * (2 * h + d)))
                total = 0
                for n in range(3):
                    index = (2 * h + d) * 3 + n
                    b = signed16(in_dB >> (16 * (3 * h + n)))
                    c = signed16(in_C >> (16 * (3 * h + n)))
                    value = signed16(qmul(self.state[index], decay) + qmul(b, x))
                    self.state[index] = value
                    total = signed16(total + qmul(value, c))
                    packed_state = packed_state | ((value & 65535) << (16 * index))
                y = signed16(total + qmul(skip, x))
                output = output | ((y & 65535) << (16 * (2 * h + d)))
        return {"out_y": output, "out_state": packed_state}
