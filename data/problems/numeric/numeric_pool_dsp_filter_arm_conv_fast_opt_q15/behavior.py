def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap32(v):
    return to_signed(v, 32)


class Q15FastConvolutionFrame:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15
    ):
        a = [
            to_signed(in_a0, 16), to_signed(in_a1, 16), to_signed(in_a2, 16), to_signed(in_a3, 16),
            to_signed(in_a4, 16), to_signed(in_a5, 16), to_signed(in_a6, 16), to_signed(in_a7, 16),
            to_signed(in_a8, 16), to_signed(in_a9, 16), to_signed(in_a10, 16), to_signed(in_a11, 16),
            to_signed(in_a12, 16), to_signed(in_a13, 16), to_signed(in_a14, 16), to_signed(in_a15, 16),
        ]
        b = [
            to_signed(in_b0, 16), to_signed(in_b1, 16), to_signed(in_b2, 16), to_signed(in_b3, 16),
            to_signed(in_b4, 16), to_signed(in_b5, 16), to_signed(in_b6, 16), to_signed(in_b7, 16),
            to_signed(in_b8, 16), to_signed(in_b9, 16), to_signed(in_b10, 16), to_signed(in_b11, 16),
            to_signed(in_b12, 16), to_signed(in_b13, 16), to_signed(in_b14, 16), to_signed(in_b15, 16),
        ]

        out = []
        for n in range(31):
            acc = 0
            for i in range(16):
                j = n - i
                if 0 <= j < 16:
                    acc = wrap32(acc + a[i] * b[j])
            value = acc >> 15
            if value > 32767:
                value = 32767
            elif value < -32768:
                value = -32768
            out.append({"out_data": to_unsigned(value, 16), "out_index": n})
        return out
