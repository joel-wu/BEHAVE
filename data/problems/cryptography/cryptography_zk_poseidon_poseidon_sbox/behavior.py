P = 21888242871839275222246405745257275088548364400416034343698204186575808495617


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class PoseidonSbox:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_in):
        x = in_in % P
        in2 = (x * x) % P
        in4 = (in2 * in2) % P
        out_out = (in4 * x) % P
        return {"out_out": to_unsigned(out_out, 254)}