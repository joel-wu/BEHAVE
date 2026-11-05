def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographySymAesAesShiftrows:
    def __init__(self):
        self.reset()

    def reset(self):
        self.idle = 1

    def process(self, in_state):
        in_state = to_unsigned(in_state, 128)
        b0 = (in_state >> 120) & 0xFF
        b1 = (in_state >> 112) & 0xFF
        b2 = (in_state >> 104) & 0xFF
        b3 = (in_state >> 96) & 0xFF
        b4 = (in_state >> 88) & 0xFF
        b5 = (in_state >> 80) & 0xFF
        b6 = (in_state >> 72) & 0xFF
        b7 = (in_state >> 64) & 0xFF
        b8 = (in_state >> 56) & 0xFF
        b9 = (in_state >> 48) & 0xFF
        b10 = (in_state >> 40) & 0xFF
        b11 = (in_state >> 32) & 0xFF
        b12 = (in_state >> 24) & 0xFF
        b13 = (in_state >> 16) & 0xFF
        b14 = (in_state >> 8) & 0xFF
        b15 = in_state & 0xFF

        out_state = (
            (b0 << 120) |
            (b5 << 112) |
            (b10 << 104) |
            (b15 << 96) |
            (b4 << 88) |
            (b9 << 80) |
            (b14 << 72) |
            (b3 << 64) |
            (b8 << 56) |
            (b13 << 48) |
            (b2 << 40) |
            (b7 << 32) |
            (b12 << 24) |
            (b1 << 16) |
            (b6 << 8) |
            b11
        )
        return {"out_state": to_unsigned(out_state, 128)}