def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AESMixColumns:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_state):
        b = [(in_state >> (8 * (15 - i))) & 0xFF for i in range(16)]

        def mul2(x):
            y = (x << 1) & 0xFF
            if x & 0x80:
                y ^= 0x1B
            return y

        def mul3(x):
            return mul2(x) ^ x

        o = [0] * 16
        for c in range(4):
            a0 = b[4 * c + 0]
            a1 = b[4 * c + 1]
            a2 = b[4 * c + 2]
            a3 = b[4 * c + 3]
            o[4 * c + 0] = mul2(a0) ^ mul3(a1) ^ a2 ^ a3
            o[4 * c + 1] = a0 ^ mul2(a1) ^ mul3(a2) ^ a3
            o[4 * c + 2] = a0 ^ a1 ^ mul2(a2) ^ mul3(a3)
            o[4 * c + 3] = mul3(a0) ^ a1 ^ a2 ^ mul2(a3)

        out_state = 0
        for x in o:
            out_state = ((out_state << 8) | x) & ((1 << 128) - 1)

        return {"out_state": to_unsigned(out_state, 128)}