def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def saturate_signed_16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


class Q15LmsAdaptiveFilterStep:
    def __init__(self):
        self.reset()

    def reset(self):
        self.coeff = [0, 0, 0, 0]
        self.state = [0, 0, 0, 0]

    def process(self, in_sample, in_ref, in_mu, in_post_shift):
        coeff = self.coeff[:]
        state = self.state[:]

        x_new = to_signed(in_sample, 16)
        ref = to_signed(in_ref, 16)
        mu = to_signed(in_mu, 16)
        post_shift = to_unsigned(in_post_shift, 4)

        x = [state[0], state[1], state[2], x_new]

        acc = x[0] * coeff[0] + x[1] * coeff[1] + x[2] * coeff[2] + x[3] * coeff[3]

        lShift = 15 - post_shift
        y = saturate_signed_16(acc >> lShift)

        e = to_signed(ref - y, 16)

        alpha = to_signed((e * mu) >> 15, 16)

        coeff_next = [0, 0, 0, 0]
        for i in range(4):
            delta_i = (alpha * x[i]) >> 15
            coeff_next[i] = saturate_signed_16(coeff[i] + delta_i)

        state_next = [x[1], x[2], x[3], 0]

        self.coeff = coeff_next
        self.state = state_next

        return {
            "out_sample": to_unsigned(y, 16),
            "out_error": to_unsigned(e, 16),
        }