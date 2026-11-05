def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    return v - (1 << b) if v & (1 << (b - 1)) else v


def wrap32(v):
    return to_signed(v, 32)


def sat_q15(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


class Q15CorrelationFast4:
    def __init__(self, mem_depth=2048):
        self.mem_depth = mem_depth
        self.reset()

    def reset(self):
        self.data = [0] * self.mem_depth

    def process(self, in_srcA_addr, in_srcB_addr, in_dst_addr):
        if in_srcA_addr + 4 > self.mem_depth:
            return {"out_dst_len": 0}
        if in_srcB_addr + 4 > self.mem_depth:
            return {"out_dst_len": 0}
        if in_dst_addr + 7 > self.mem_depth:
            return {"out_dst_len": 0}

        a0 = to_signed(self.data[in_srcA_addr + 0], 16)
        a1 = to_signed(self.data[in_srcA_addr + 1], 16)
        a2 = to_signed(self.data[in_srcA_addr + 2], 16)
        a3 = to_signed(self.data[in_srcA_addr + 3], 16)

        b0 = to_signed(self.data[in_srcB_addr + 0], 16)
        b1 = to_signed(self.data[in_srcB_addr + 1], 16)
        b2 = to_signed(self.data[in_srcB_addr + 2], 16)
        b3 = to_signed(self.data[in_srcB_addr + 3], 16)

        acc0 = wrap32(a0 * b3)
        r0 = sat_q15(acc0 >> 15)

        acc1 = wrap32((a0 * b2) + (a1 * b3))
        r1 = sat_q15(acc1 >> 15)

        acc2 = wrap32(wrap32((a0 * b1) + (a1 * b2)) + (a2 * b3))
        r2 = sat_q15(acc2 >> 15)

        acc3 = wrap32(wrap32(wrap32((a0 * b0) + (a1 * b1)) + (a2 * b2)) + (a3 * b3))
        r3 = sat_q15(acc3 >> 15)

        acc4 = wrap32(wrap32((a1 * b0) + (a2 * b1)) + (a3 * b2))
        r4 = sat_q15(acc4 >> 15)

        acc5 = wrap32((a2 * b0) + (a3 * b1))
        r5 = sat_q15(acc5 >> 15)

        acc6 = wrap32(a3 * b0)
        r6 = sat_q15(acc6 >> 15)

        self.data[in_dst_addr + 0] = to_unsigned(r0, 16)
        self.data[in_dst_addr + 1] = to_unsigned(r1, 16)
        self.data[in_dst_addr + 2] = to_unsigned(r2, 16)
        self.data[in_dst_addr + 3] = to_unsigned(r3, 16)
        self.data[in_dst_addr + 4] = to_unsigned(r4, 16)
        self.data[in_dst_addr + 5] = to_unsigned(r5, 16)
        self.data[in_dst_addr + 6] = to_unsigned(r6, 16)

        return {"out_dst_len": 7}