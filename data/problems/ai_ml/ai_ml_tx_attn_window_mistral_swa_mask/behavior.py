def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlTxAttnWindowMistralSwaMask:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_query_position, in_key_position, in_sliding_window):
        q = to_unsigned(in_query_position, 16)
        k = to_unsigned(in_key_position, 16)
        w = to_unsigned(in_sliding_window, 16)
        out_allow = 0
        if k <= q:
            if (q - k) < w:
                out_allow = 1
        return {"out_allow": to_unsigned(out_allow, 1)}