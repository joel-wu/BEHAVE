def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DataSystemsVectorSearchPQFastScanLut4Step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_codes_packed, in_lut, in_accum):
        out_accum = 0
        for j in range(32):
            lane = j & 15
            address = 2 * lane if lane < 8 else 2 * (lane - 8) + 1
            byte_j = (in_codes_packed >> (8 * address)) & 0xFF
            acc_j = (in_accum >> (16 * j)) & 0xFFFF
            if j < 16:
                code_j = byte_j & 0xF
            else:
                code_j = (byte_j >> 4) & 0xF
            addend_j = (in_lut >> (8 * code_j)) & 0xFF
            sum_j = to_unsigned(acc_j + addend_j, 16)
            out_accum |= sum_j << (16 * j)
        return {"out_accum": to_unsigned(out_accum, 512)}
