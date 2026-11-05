def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Gemma2AlternatingSlidingWindowSelector:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_layer_idx):
        return {"out_is_sliding_attention": 1 - (to_unsigned(in_layer_idx, 32) & 1)}
