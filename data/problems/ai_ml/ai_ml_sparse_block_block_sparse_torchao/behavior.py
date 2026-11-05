def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlSparseBlockBlockSparseTorchao:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_M,
        in_K,
        in_N,
        in_num_block_rows,
        in_nnz_blocks,
        in_crow_indices,
        in_col_indices,
        in_values,
        in_x,
    ):
        # Domain restriction: M, K, N capped at 4; num_block_rows capped at 2;
        # nnz_blocks capped at 4. Out-of-domain inputs produce all-zero output.
        if in_M > 4 or in_K > 4 or in_N > 4 or in_num_block_rows > 2 or in_nnz_blocks > 4:
            return {"out_y": 0}

        crow = [(in_crow_indices >> (8 * i)) & 0xFF for i in range(5)]
        col = [(in_col_indices >> (3 * i)) & 0x7 for i in range(8)]
        values = []
        for p in range(8):
            base = 32 * p
            values.append(
                [
                    [to_signed((in_values >> (base + 0)) & 0xFF, 8), to_signed((in_values >> (base + 8)) & 0xFF, 8)],
                    [to_signed((in_values >> (base + 16)) & 0xFF, 8), to_signed((in_values >> (base + 24)) & 0xFF, 8)],
                ]
            )
        x = [
            [to_signed((in_x >> (8 * (8 * k + n))) & 0xFF, 8) for n in range(8)]
            for k in range(8)
        ]

        W = [[0 for _ in range(8)] for _ in range(8)]
        for br in range(2):
            if br < in_num_block_rows:
                start = crow[br]
                stop = crow[br + 1]
                for p in range(4):
                    if start <= p and p < stop and p < in_nnz_blocks:
                        c = col[p]
                        if c < 4:
                            W[2 * br + 0][2 * c + 0] = values[p][0][0]
                            W[2 * br + 0][2 * c + 1] = values[p][0][1]
                            W[2 * br + 1][2 * c + 0] = values[p][1][0]
                            W[2 * br + 1][2 * c + 1] = values[p][1][1]

        out_y = 0
        for m in range(4):
            for n in range(4):
                acc = 0
                if m < in_M and n < in_N:
                    for k in range(4):
                        if k < in_K:
                            acc = to_signed(acc + W[m][k] * x[k][n], 32)
                out_y |= to_unsigned(acc, 8) << (8 * (8 * m + n))

        return {"out_y": to_unsigned(out_y, 512)}