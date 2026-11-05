def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class cryptography_pqc_sphincs_sphincs_wots_chain_step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_chain_idx, in_msg):
        msg = to_unsigned(in_msg, 256)
        idx = to_unsigned(in_chain_idx, 7)

        digits = [0] * 67
        csum = 0

        for k in range(32):
            byte = (msg >> (8 * (31 - k))) & 0xFF
            hi = (byte >> 4) & 0xF
            lo = byte & 0xF
            digits[2 * k] = hi
            digits[2 * k + 1] = lo
            csum += 15 - hi
            csum += 15 - lo

        csum_aligned = csum << 4
        c0 = (csum_aligned >> 8) & 0xFF
        c1 = csum_aligned & 0xFF
        digits[64] = (c0 >> 4) & 0xF
        digits[65] = c0 & 0xF
        digits[66] = (c1 >> 4) & 0xF

        out_chain_step = digits[idx] if idx <= 66 else 0
        return {"out_chain_step": to_unsigned(out_chain_step, 4)}