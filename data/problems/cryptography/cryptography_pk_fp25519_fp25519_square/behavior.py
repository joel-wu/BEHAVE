P = (1 << 255) - 19


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyPkFp25519Fp25519Square:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x):
        x = in_x % P
        z = (x * x) % P
        return {"out_z": to_unsigned(z, 256)}