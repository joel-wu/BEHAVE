def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class QuaternionConjugateFp32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_q0, in_q1, in_q2, in_q3):
        return {
            "out_q0": to_unsigned(in_q0, 32),
            "out_q1": to_unsigned(in_q1 ^ 0x80000000, 32),
            "out_q2": to_unsigned(in_q2 ^ 0x80000000, 32),
            "out_q3": to_unsigned(in_q3 ^ 0x80000000, 32),
        }