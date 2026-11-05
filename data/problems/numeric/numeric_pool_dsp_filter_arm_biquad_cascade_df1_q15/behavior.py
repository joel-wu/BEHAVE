def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


STAGE_COEFFS = [
    {"b0": 0x2000, "b1": 0x1000, "b2": 0x0800, "a1": 0x0400, "a2": 0x0200},
    {"b0": 0x1800, "b1": 0x0C00, "b2": 0x0600, "a1": 0x0300, "a2": 0x0100},
]


class BiquadCascadeDf1Q15Sample:
    def __init__(self):
        self.reset()

    def reset(self):
        self.Xn1 = [0, 0]
        self.Xn2 = [0, 0]
        self.Yn1 = [0, 0]
        self.Yn2 = [0, 0]

    def process(self, in_x):
        stage_input = to_signed(in_x, 16)

        old_Xn1 = [self.Xn1[0], self.Xn1[1]]
        old_Xn2 = [self.Xn2[0], self.Xn2[1]]
        old_Yn1 = [self.Yn1[0], self.Yn1[1]]
        old_Yn2 = [self.Yn2[0], self.Yn2[1]]

        for s in range(2):
            coeffs = STAGE_COEFFS[s]
            b0 = to_signed(coeffs["b0"], 16)
            b1 = to_signed(coeffs["b1"], 16)
            b2 = to_signed(coeffs["b2"], 16)
            a1 = to_signed(coeffs["a1"], 16)
            a2 = to_signed(coeffs["a2"], 16)

            acc = (
                b0 * stage_input
                + b1 * old_Xn1[s]
                + b2 * old_Xn2[s]
                + a1 * old_Yn1[s]
                + a2 * old_Yn2[s]
            )

            stage_output = acc >> 15
            if stage_output > 32767:
                stage_output = 32767
            elif stage_output < -32768:
                stage_output = -32768

            self.Xn2[s] = old_Xn1[s]
            self.Xn1[s] = stage_input
            self.Yn2[s] = old_Yn1[s]
            self.Yn1[s] = stage_output

            stage_input = stage_output

        return {"out_y": to_unsigned(stage_input, 16)}