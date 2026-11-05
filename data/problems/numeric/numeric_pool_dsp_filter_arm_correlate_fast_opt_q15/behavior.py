def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat_q15(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


class CorrelateFastOptQ15SingleOutput:
    def __init__(self):
        self.reset()

    def reset(self):
        self.a = [0] * 256
        self.b = [0] * 256

    def process(self, in_len_a, in_len_b, in_a_base, in_b_base, in_out_index):
        len_a = in_len_a
        len_b = in_len_b
        k = in_out_index

        if len_a == 0 or len_b == 0:
            return {"out_sample": 0, "out_nonzero_region": 0}

        if len_a >= len_b:
            l = len_a
            s = len_b
            x_mem = self.a
            y_mem = self.b
            x_base = in_a_base
            y_base = in_b_base
            z = l - s
            ncorr = l + s - 1
            ndst = (l << 1) - 1
            if k >= ndst or k < z:
                return {"out_sample": 0, "out_nonzero_region": 0}
            n = k - z
        else:
            l = len_b
            s = len_a
            x_mem = self.b
            y_mem = self.a
            x_base = in_b_base
            y_base = in_a_base
            ncorr = l + s - 1
            ndst = (l << 1) - 1
            if k >= ndst or k >= ncorr:
                return {"out_sample": 0, "out_nonzero_region": 0}
            n = ncorr - 1 - k

        acc = 0
        offset = s - 1
        for t in range(s):
            u = n + t - offset
            if 0 <= u < l:
                x_val = to_signed(x_mem[to_unsigned(x_base + u, 8)], 16)
            else:
                x_val = 0
            y_val = to_signed(y_mem[to_unsigned(y_base + t, 8)], 16)
            acc = to_signed(acc + x_val * y_val, 32)

        return {"out_sample": to_unsigned(sat_q15(acc >> 15), 16), "out_nonzero_region": 1}