def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlTxAttnGqaGqaGroupedKv8x:
    def __init__(self, head_dim=16, elem_w=16):
        self.head_dim = head_dim
        self.elem_w = elem_w
        self.reset()

    def reset(self):
        self.pending = []

    def process(self, in_key, in_value):
        width = self.head_dim * self.elem_w
        key = to_unsigned(in_key, width)
        value = to_unsigned(in_value, width)
        return [
            {"out_key": key, "out_value": value, "out_replica": i}
            for i in range(8)
        ]