

class HexlMontgomeryReduce64:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_T_hi, in_T_lo, in_q, in_r, in_mod_R_msk, in_inv_mod):
        mask64 = (1 << 64) - 1
        hi, lo, q = in_T_hi & mask64, in_T_lo & mask64, in_q & mask64
        r, mask, inv = in_r & 127, in_mod_R_msk & mask64, in_inv_mod & mask64
        if r < 1 or r > 63:
            return {"out_S": 0, "out_error": 1}
        radix = 1 << r
        t = (hi << 64) | lo
        if q == 0 or q >= radix or not (q & 1) or mask != radix-1 or inv >= radix or (q*inv & mask) != mask or t >= radix*q:
            return {"out_S": 0, "out_error": 1}
        m = (lo*inv) & mask
        u = (t+m*q) >> r
        return {"out_S": u-q if u >= q else u, "out_error": 0}
