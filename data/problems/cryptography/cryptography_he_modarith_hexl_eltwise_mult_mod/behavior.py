

class HexlScalarMultiplyMod:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_y_operand, in_y_barrett_factor, in_modulus):
        mask = (1 << 64) - 1
        x, y, f, q = in_x & mask, in_y_operand & mask, in_y_barrett_factor & mask, in_modulus & mask
        target = y << 64
        if q < 2 or q >= (1 << 63) or y >= q or f*q > target or (f+1)*q <= target:
            return {"out_value": 0, "out_error": 1}
        r = x*y - ((x*f) >> 64)*q
        return {"out_value": r-q if r >= q else r, "out_error": 0}
