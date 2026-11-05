from fp_lib import fp32_add, fp32_mul

def to_unsigned(v, b):
    return v & ((1 << b) - 1)

class MatrixMultiplyF32Cell:
    def __init__(self):
        self.a_mem = [0] * 65536
        self.b_mem = [0] * 65536

    def reset(self):
        pass

    def process(self, in_num_rows_a, in_num_cols_a, in_num_rows_b, in_num_cols_b, in_row_idx, in_col_idx):
        valid = (
            in_num_cols_a == in_num_rows_b and
            in_row_idx < in_num_rows_a and
            in_col_idx < in_num_cols_b
        )
        if not valid:
            return {"out_status": 1, "out_value": 0}

        result = 0
        for k in range(256):
            if k < in_num_cols_a:
                a_val = self.a_mem[in_row_idx * in_num_cols_a + k]
                b_val = self.b_mem[k * in_num_cols_b + in_col_idx]
                product = fp32_mul(a_val, b_val)
                if k == 0:
                    result = product
                else:
                    result = fp32_add(result, product)

        return {"out_status": 0, "out_value": to_unsigned(result, 32)}
