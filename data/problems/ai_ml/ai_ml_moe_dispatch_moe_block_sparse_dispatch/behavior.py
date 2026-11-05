"""Stable expert routing and complete bounded CSR/transpose topology."""


def pack(values, width):
    result = 0
    for i in range(len(values)):
        result |= values[i] << (i * width)
    return result


class MoEBlockSparseDispatch:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_top_experts):
        experts = [(in_top_experts >> (2*i)) & 3 for i in range(16)]
        indices = [i for i in range(16)]
        for i in range(16):
            for j in range(15):
                if experts[indices[j]] > experts[indices[j+1]]:
                    temp = indices[j]
                    indices[j] = indices[j+1]
                    indices[j+1] = temp
        counts = [0]*4
        for i in range(16):
            counts[experts[i]] += 1
        bins = [0]*4
        padded = [0]*4
        columns = [0]*8
        rows = [0]*8
        offsets = [0]*5
        columns_t = [0]*8
        offsets_t = [0]*9
        permutation_t = [0]*8
        total = 0
        block_rows = 0
        for e in range(4):
            total += counts[e]
            bins[e] = total
            if counts[e] != 0:
                for k in range(2):
                    entry = block_rows*2+k
                    columns[entry] = e*2+k
                    rows[entry] = block_rows
                block_rows += 1
            padded[e] = block_rows*128
        nnz = block_rows*2
        for r in range(5):
            offsets[r] = min(r, block_rows)*2
        # Stable counting transpose, including empty expert columns.
        cursor = 0
        for c in range(8):
            for entry in range(8):
                if entry < nnz and columns[entry] == c:
                    columns_t[cursor] = rows[entry]
                    permutation_t[cursor] = entry
                    cursor += 1
            offsets_t[c+1] = cursor
        return {
            "out_indices": pack(indices, 4),
            "out_bin_ids": pack([experts[indices[i]] for i in range(16)], 2),
            "out_bins": pack(bins, 5),
            "out_padded_bins": pack(padded, 10),
            "out_tokens_per_expert": pack(counts, 5),
            "out_block_rows": block_rows,
            "out_offsets": pack(offsets, 4),
            "out_column_indices": pack(columns, 3),
            "out_row_indices": pack(rows, 2),
            "out_column_indices_t": pack(columns_t, 2),
            "out_offsets_t": pack(offsets_t, 4),
            "out_block_offsets_t": pack(permutation_t, 3),
        }
