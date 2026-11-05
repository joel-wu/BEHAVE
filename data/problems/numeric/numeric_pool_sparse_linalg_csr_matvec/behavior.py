def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CsrMatvec4Lane:
    NNZ_PER_ROW = 2

    def __init__(self):
        self.row_ptr = [0] * 512
        self.col_idx = [0] * 1024
        self.a_val = [0] * 1024
        self.x_vec = [0] * 1024
        self.b_vec = [0] * 1024

    def reset(self):
        # These views belong to the external memory environment.
        pass

    def _process_entry(self, k, sum0, sum1, sum2, sum3):
        c = to_unsigned(self.col_idx[k], 8)
        a = to_signed(self.a_val[k], 16)
        x_pack = to_unsigned(self.x_vec[c], 64)
        x = [
            to_signed(x_pack >> 0,  16),
            to_signed(x_pack >> 16, 16),
            to_signed(x_pack >> 32, 16),
            to_signed(x_pack >> 48, 16),
        ]
        return (
            to_signed(to_unsigned(sum0 + a * x[0], 32), 32),
            to_signed(to_unsigned(sum1 + a * x[1], 32), 32),
            to_signed(to_unsigned(sum2 + a * x[2], 32), 32),
            to_signed(to_unsigned(sum3 + a * x[3], 32), 32),
        )

    def process(self, in_row, in_alpha, in_beta):
        r = to_unsigned(in_row, 8)
        alpha = to_signed(in_alpha, 16)
        beta = to_signed(in_beta, 16)
        # Only row_ptr[r] is consulted (NNZ_PER_ROW is fixed at 2).
        row_start = to_unsigned(self.row_ptr[r], 9)
        b_pack = to_unsigned(self.b_vec[r], 64)
        b = [
            to_signed(b_pack >> 0,  16),
            to_signed(b_pack >> 16, 16),
            to_signed(b_pack >> 32, 16),
            to_signed(b_pack >> 48, 16),
        ]
        sum0 = 0
        sum1 = 0
        sum2 = 0
        sum3 = 0
        # Two fixed CSR-entry accesses at row_start and row_start + 1.
        # The second access wraps in 10 bits per the spec (col_idx /
        # a_val arrays are 1024-deep).
        for offset in range(self.NNZ_PER_ROW):
            k = (row_start + offset) & 0x3FF
            sum0, sum1, sum2, sum3 = self._process_entry(
                k, sum0, sum1, sum2, sum3)
        sums = (sum0, sum1, sum2, sum3)
        finals = [
            alpha * to_signed(to_unsigned(sums[lane], 16), 16) + beta * b[lane]
            for lane in range(4)
        ]
        return {
            "out_y0": to_unsigned(finals[0], 16),
            "out_y1": to_unsigned(finals[1], 16),
            "out_y2": to_unsigned(finals[2], 16),
            "out_y3": to_unsigned(finals[3], 16),
        }
