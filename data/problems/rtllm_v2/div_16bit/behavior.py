class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        quotient = 0
        remainder = 0
        for i in range(16):
            remainder = (remainder << 1) | ((A >> (15 - i)) & 1)
            quotient <<= 1
            if remainder >= B:
                remainder -= B
                quotient |= 1
        return {"result": quotient, "odd": remainder}
