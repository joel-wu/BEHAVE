P = 0x30644E72E131A029B85045B68181585D97816A916871CA8D3C208C16D87CFD47

def to_unsigned(v, b):
    return v & ((1 << b) - 1)

class CryptographyZkBn254Bn254FpAdd:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_sum = 0

    def process(self, in_a, in_b):
        raw_sum = in_a + in_b
        reduced_sum = raw_sum - P if raw_sum >= P else raw_sum
        if reduced_sum >= P:
            reduced_sum -= P
        self.out_sum = to_unsigned(reduced_sum, 254)
        return {"out_sum": self.out_sum}
