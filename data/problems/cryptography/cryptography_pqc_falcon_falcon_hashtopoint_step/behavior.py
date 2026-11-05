def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class FalconHashToPointStep:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_w):
        w = to_unsigned(in_w, 16)
        if w >= 61445:
            return None
        for _ in range(4):
            if w >= 12289:
                w -= 12289
        return {"out_x": to_unsigned(w, 14)}
