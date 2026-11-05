def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DataSystemsDistanceHammingDist64bPopcount:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_bs1, in_bs2):
        diff = to_unsigned(in_bs1, 64) ^ to_unsigned(in_bs2, 64)
        distance = 0
        for i in range(64):
            distance += (diff >> i) & 1
        return {"out_distance": to_unsigned(distance, 7)}