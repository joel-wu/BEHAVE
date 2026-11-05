P = 21888242871839275222246405745257275088548364400416034343698204186575808495617
N0 = 0x73f82f1d0d8341b2e39a9828990623916586864b4c6911b3c2e1f593efffffff


class Bn254FrMontgomeryMultiply:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b):
        a, b = in_a & ((1 << 254)-1), in_b & ((1 << 254)-1)
        if a >= P or b >= P:
            return {"out_c": 0, "out_error": 1}
        t = a*b
        m = (t*N0) & ((1 << 256)-1)
        u = (t+m*P) >> 256
        return {"out_c": u-P if u >= P else u, "out_error": 0}
