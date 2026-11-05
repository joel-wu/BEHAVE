def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlQuantFp8Fp8AmaxHistory:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_h0, in_h1, in_h2, in_h3):
        return {"out_h0": 0, "out_h1": to_unsigned(in_h2, 32),
                "out_h2": to_unsigned(in_h3, 32), "out_h3": to_unsigned(in_h0, 32)}
