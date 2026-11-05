def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlTxAttnGqaGqaGroupedKv2x:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_group_id, in_seq_id, in_kv_head_id, in_head_dim, in_key, in_value):
        group_id = to_unsigned(in_group_id, 16)
        seq_id = to_unsigned(in_seq_id, 16)
        kv_head_id = to_unsigned(in_kv_head_id, 16)
        head_dim = to_unsigned(in_head_dim, 16)
        key = to_unsigned(in_key, 256)
        value = to_unsigned(in_value, 256)
        head0 = to_unsigned(kv_head_id << 1, 16)
        head1 = to_unsigned((kv_head_id << 1) + 1, 16)
        return [
            {
                "out_group_id": group_id,
                "out_seq_id": seq_id,
                "out_attention_head_id": head0,
                "out_head_dim": head_dim,
                "out_key": key,
                "out_value": value,
            },
            {
                "out_group_id": group_id,
                "out_seq_id": seq_id,
                "out_attention_head_id": head1,
                "out_head_dim": head_dim,
                "out_key": key,
                "out_value": value,
            },
        ]