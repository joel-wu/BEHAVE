def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Pipelined64BitAdder:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, adda, addb):
        a = to_unsigned(adda, 64)
        b = to_unsigned(addb, 64)

        t0 = (a & 0xFFFF) + (b & 0xFFFF)
        sum0 = t0 & 0xFFFF
        carry0 = (t0 >> 16) & 1

        t1 = ((a >> 16) & 0xFFFF) + ((b >> 16) & 0xFFFF) + carry0
        sum1 = t1 & 0xFFFF
        carry1 = (t1 >> 16) & 1

        t2 = ((a >> 32) & 0xFFFF) + ((b >> 32) & 0xFFFF) + carry1
        sum2 = t2 & 0xFFFF
        carry2 = (t2 >> 16) & 1

        t3 = ((a >> 48) & 0xFFFF) + ((b >> 48) & 0xFFFF) + carry2
        sum3 = t3 & 0xFFFF
        carry3 = (t3 >> 16) & 1

        result = sum0 | (sum1 << 16) | (sum2 << 32) | (sum3 << 48) | (carry3 << 64)
        return {"result": to_unsigned(result, 65)}
