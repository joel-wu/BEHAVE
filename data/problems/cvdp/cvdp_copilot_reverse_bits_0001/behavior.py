class ReverseBits:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, num_in):
        result = 0
        for i in range(32):
            result |= ((num_in >> i) & 1) << (31 - i)
        return {"num_out": result}
