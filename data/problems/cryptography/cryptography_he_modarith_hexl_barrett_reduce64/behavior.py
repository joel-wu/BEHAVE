

class HexlBarrettReduce64:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_input, in_modulus, in_q_barr):
        q = in_modulus & ((1 << 64) - 1)
        f = in_q_barr & ((1 << 64) - 1)
        x = in_input & ((1 << 64) - 1)
        if q < 2 or f*q > (1 << 64) or (f+1)*q <= (1 << 64):
            return {"out_value": 0, "out_error": 1}
        return {"out_value": x - ((x*f) >> 64)*q, "out_error": 0}
