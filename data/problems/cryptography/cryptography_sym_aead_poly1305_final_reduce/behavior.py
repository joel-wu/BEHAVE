def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Poly1305FinalReduce:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_h0, in_h1, in_h2, in_h3, in_h4, in_s0, in_s1, in_s2, in_s3):
        h0 = to_unsigned(in_h0, 32)
        h1 = to_unsigned(in_h1, 32)
        h2 = to_unsigned(in_h2, 32)
        h3 = to_unsigned(in_h3, 32)
        h4 = to_unsigned(in_h4, 32)
        s0 = to_unsigned(in_s0, 32)
        s1 = to_unsigned(in_s1, 32)
        s2 = to_unsigned(in_s2, 32)
        s3 = to_unsigned(in_s3, 32)

        for _ in range(2):
            g0 = to_unsigned(h0 + 5, 32)
            carry0 = 1 if g0 < h0 else 0

            g1 = to_unsigned(h1 + carry0, 32)
            carry1 = 1 if g1 < h1 else 0

            g2 = to_unsigned(h2 + carry1, 32)
            carry2 = 1 if g2 < h2 else 0

            g3 = to_unsigned(h3 + carry2, 32)
            carry3 = 1 if g3 < h3 else 0

            g4 = to_unsigned(h4 + carry3 - 4, 32)

            if ((g4 >> 31) & 1) == 0:
                h0, h1, h2, h3, h4 = g0, g1, g2, g3, g4

        sum0 = h0 + s0
        out0 = to_unsigned(sum0, 32)
        carry0 = (sum0 >> 32) & 1

        sum1 = h1 + s1 + carry0
        out1 = to_unsigned(sum1, 32)
        carry1 = (sum1 >> 32) & 1

        sum2 = h2 + s2 + carry1
        out2 = to_unsigned(sum2, 32)
        carry2 = (sum2 >> 32) & 1

        sum3 = h3 + s3 + carry2
        out3 = to_unsigned(sum3, 32)

        return {
            "out_h0": out0,
            "out_h1": out1,
            "out_h2": out2,
            "out_h3": out3,
        }