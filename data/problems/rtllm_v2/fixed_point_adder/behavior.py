class Behavior:
    def __init__(self, Q=15, N=32):
        self.Q = Q
        self.N = N

    def reset(self):
        pass

    def process(self, a, b):
        width = self.N - 1
        mask = (1 << width) - 1
        sa = a >> width
        sb = b >> width
        ma = a & mask
        mb = b & mask
        if sa == sb:
            magnitude = (ma + mb) & mask
            sign = sa
        elif ma > mb:
            magnitude = ma - mb
            sign = sa
        elif mb > ma:
            magnitude = mb - ma
            sign = sb
        else:
            magnitude = 0
            sign = 0
        return {"c": (sign << width) | magnitude}
