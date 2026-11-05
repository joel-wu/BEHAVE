def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class MulticycleMul4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b):
        a = to_unsigned(in_a, 4)
        b = to_unsigned(in_b, 4)
        return {"out_p": to_unsigned(a * b, 8)}
