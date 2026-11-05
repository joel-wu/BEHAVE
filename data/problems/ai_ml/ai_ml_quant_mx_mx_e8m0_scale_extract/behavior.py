def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class MXE8M0ScaleExtract:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_hp_data_M, in_hp_data_K, in_scale_index, in_scale_value):
        hp_data_M = to_unsigned(in_hp_data_M, 16)
        hp_data_K = to_unsigned(in_hp_data_K, 16)
        scale_index = to_unsigned(in_scale_index, 32)
        scale_value = to_unsigned(in_scale_value, 8)

        n_row_blocks = 0 if hp_data_M == 0 else ((hp_data_M - 1) // 128) + 1
        n_col_blocks = 0 if hp_data_K == 0 else ((hp_data_K - 1) // 128) + 1

        scale_rows_math = n_row_blocks * 32
        scale_cols_math = n_col_blocks * 16
        scale_count = scale_rows_math * scale_cols_math
        in_range = 1 if scale_index < scale_count else 0

        return {
            "out_scale_rows": to_unsigned(scale_rows_math, 16),
            "out_scale_cols": to_unsigned(scale_cols_math, 16),
            "out_in_range": in_range,
            "out_scale_value": scale_value if in_range else 0,
        }