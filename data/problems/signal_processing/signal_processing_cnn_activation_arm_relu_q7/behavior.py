def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingCnnActivationArmReluQ7:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        x = to_signed(in_data, 8)
        y = 0 if x < 0 else x
        return {"out_data": to_unsigned(y, 8)}