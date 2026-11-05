class CsrMatrixAddRowMerge:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_row,
        in_alpha,
        in_beta,
        in_a_row_start,
        in_a_row_end,
        in_a_col0,
        in_a_col1,
        in_a_col2,
        in_a_col3,
        in_a_val0,
        in_a_val1,
        in_a_val2,
        in_a_val3,
        in_b_row_start,
        in_b_row_end,
        in_b_col0,
        in_b_col1,
        in_b_col2,
        in_b_col3,
        in_b_val0,
        in_b_val1,
        in_b_val2,
        in_b_val3,
    ):
        cols = [in_a_col0, in_a_col1, in_a_col2, in_a_col3,
                in_b_col0, in_b_col1, in_b_col2, in_b_col3]
        vals = [in_a_val0, in_a_val1, in_a_val2, in_a_val3,
                in_b_val0, in_b_val1, in_b_val2, in_b_val3]
        result_cols = [0] * 8
        result_vals = [0] * 8
        count = 0
        for k in range(8):
            active = (in_a_row_start <= k and k < in_a_row_end) if k < 4 else (in_b_row_start <= k-4 and k-4 < in_b_row_end)
            if active:
                term = ((in_alpha if k < 4 else in_beta) * vals[k]) & 0xffffffff
                position = count
                for j in range(8):
                    if j < count and result_cols[j] == cols[k]:
                        position = j
                if position == count:
                    result_cols[count] = cols[k]
                    count += 1
                result_vals[position] = (result_vals[position] + term) & 0xffffffff
        return {
            "out_row": in_row, "out_nnz": count,
            "out_col0": result_cols[0],
            "out_col1": result_cols[1],
            "out_col2": result_cols[2],
            "out_col3": result_cols[3],
            "out_col4": result_cols[4],
            "out_col5": result_cols[5],
            "out_col6": result_cols[6],
            "out_col7": result_cols[7],
            "out_val0": result_vals[0],
            "out_val1": result_vals[1],
            "out_val2": result_vals[2],
            "out_val3": result_vals[3],
            "out_val4": result_vals[4],
            "out_val5": result_vals[5],
            "out_val6": result_vals[6],
            "out_val7": result_vals[7],
        }
