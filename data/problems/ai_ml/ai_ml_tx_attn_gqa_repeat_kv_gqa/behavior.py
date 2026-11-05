def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlTxAttnGqaRepeatKvGqa:
    def __init__(self):
        self.reset()

    def reset(self):
        self.dummy = 0

    def process(
        self,
        in_data,
        in_batch_idx,
        in_kv_head_idx,
        in_seq_idx,
        in_head_dim_idx,
        in_n_rep,
    ):
        n_rep = to_unsigned(in_n_rep, 4)
        if n_rep == 0:
            return None

        data = to_unsigned(in_data, 32)
        batch_idx = to_unsigned(in_batch_idx, 16)
        kv_head_idx = to_unsigned(in_kv_head_idx, 16)
        seq_idx = to_unsigned(in_seq_idx, 16)
        head_dim_idx = to_unsigned(in_head_dim_idx, 16)

        base = kv_head_idx * n_rep
        return [
            {
                "out_data": data,
                "out_batch_idx": batch_idx,
                "out_attn_head_idx": base + r,
                "out_seq_idx": seq_idx,
                "out_head_dim_idx": head_dim_idx,
            }
            for r in range(n_rep)
        ]
