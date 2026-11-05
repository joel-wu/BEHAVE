class GfMultiplier:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        result = 0
        multiplicand = A
        for i in range(4):
            if (B >> i) & 1:
                result ^= multiplicand
            carry = multiplicand & 8
            multiplicand = ((multiplicand << 1) & 15) ^ (3 if carry else 0)
        return {"result": result}
