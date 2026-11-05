def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Popcount32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        popcount = 0
        for i in range(32):
            popcount += (in_data >> i) & 1
        return {"out_popcount": to_unsigned(popcount, 6)}