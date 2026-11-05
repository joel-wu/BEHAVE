def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class OnsetStrengthMulti:
    def __init__(self):
        self.reset()

    def reset(self):
        self.ref = [0] * 8
        self.have_ref = False

    def process(self, in_S_flat):
        S = [to_signed((in_S_flat >> (16 * f)) & 0xFFFF, 16) for f in range(8)]
        sum_pos = 0
        for f in range(8):
            diff = S[f] - self.ref[f]
            if diff > 0:
                sum_pos += diff
        onset_env = sum_pos >> 3
        if not self.have_ref:
            onset_env = 0
        self.ref = S
        self.have_ref = True
        return {"out_onset_env": to_unsigned(onset_env, 16)}
