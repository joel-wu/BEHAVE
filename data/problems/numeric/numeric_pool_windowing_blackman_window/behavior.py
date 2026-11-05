# Periodic cosine-sum window, rounded once to binary32.
WINDOW = (
    0x387BA882, 0x3B487C4D, 0x3CB20F5B, 0x3DA988C2,
    0x3E5EB075, 0x3EDEADC6, 0x3F321D9A, 0x3F6A0BB1,
    0x3F800000, 0x3F6A0BB1, 0x3F321D9A, 0x3EDEADC6,
    0x3E5EB075, 0x3DA988C2, 0x3CB20F5B, 0x3B487C4D,
)


class BlackmanHarris92dBWindow:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_index):
        return {"out_value": WINDOW[in_index & 15]}
