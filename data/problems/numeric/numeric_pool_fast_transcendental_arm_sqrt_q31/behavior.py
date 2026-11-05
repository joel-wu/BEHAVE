# Q31 sqrt with CMSIS inverse-root seeds and three fixed-point refinements.
SEEDS = (
    536870912, 506166750, 480191942, 457845052, 438353264, 421156193,
    405836263, 392075079, 379625062, 368290407, 357913941, 348367849,
    339546978, 331363921, 323745341, 316629190, 309962566, 303700050,
    297802400, 292235509, 286969573, 281978417, 277238947, 272730696,
)


class Q31Sqrt:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_value):
        x = in_value & 0xFFFFFFFF
        if x & 0x80000000:
            return {"out_root": 0, "out_status": 1}
        if x == 0:
            return {"out_root": 0, "out_status": 0}
        shift = 0
        while x < 0x20000000:
            x <<= 2
            shift += 1
        v = SEEDS[(x >> 26) - 8]
        for _ in range(3):
            t = (v * v) >> 28
            t = (x * t) >> 31
            v = (v * (0x30000000 - t)) >> 29
        return {"out_root": ((x * v) >> 28) >> shift, "out_status": 0}
