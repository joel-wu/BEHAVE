def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographySymAesAesAddroundkey:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_state, in_round_key):
        return {"out_state": to_unsigned(in_state ^ in_round_key, 128)}