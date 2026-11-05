def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap32(v):
    return to_signed(v, 32)


def sat16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def smulwb(a, b):
    a = to_signed(a, 32)
    b16 = to_signed(b & 0xFFFF, 16)
    return wrap32((a * b16) >> 16)


def smlawb(a, b, c):
    a = to_signed(a, 32)
    b = to_signed(b, 32)
    c16 = to_signed(c & 0xFFFF, 16)
    return wrap32(a + ((b * c16) >> 16))


def rshift_round_14(x):
    x = to_signed(x, 32)
    return ((x >> 13) + 1) >> 1


class SignalProcessingAudioCodecOpusSilkBiquadAlt:
    def __init__(self):
        self.reset()

    def reset(self):
        self.S0 = 0
        self.S1 = 0
        self.S2 = 0
        self.S3 = 0

    def process(
        self,
        in_sample0,
        in_sample1,
        in_B0_Q28,
        in_B1_Q28,
        in_B2_Q28,
        in_A0_Q28,
        in_A1_Q28,
        in_clear_state,
    ):
        sample0 = to_signed(in_sample0, 16)
        sample1 = to_signed(in_sample1, 16)
        B0_Q28 = to_signed(in_B0_Q28, 32)
        B1_Q28 = to_signed(in_B1_Q28, 32)
        B2_Q28 = to_signed(in_B2_Q28, 32)
        A0_Q28 = to_signed(in_A0_Q28, 32)
        A1_Q28 = to_signed(in_A1_Q28, 32)

        if in_clear_state != 0:
            S0 = 0
            S1 = 0
            S2 = 0
            S3 = 0
        else:
            S0 = self.S0
            S1 = self.S1
            S2 = self.S2
            S3 = self.S3

        negA0 = wrap32(-A0_Q28)
        negA1 = wrap32(-A1_Q28)

        A0_L_Q28 = negA0 & 0x3FFF
        A0_U_Q28 = wrap32(negA0 >> 14)
        A1_L_Q28 = negA1 & 0x3FFF
        A1_U_Q28 = wrap32(negA1 >> 14)

        out32_Q14_ch0 = wrap32(smlawb(S0, B0_Q28, sample0) << 2)
        out32_Q14_ch1 = wrap32(smlawb(S2, B0_Q28, sample1) << 2)

        next_S0 = wrap32(S1 + rshift_round_14(smulwb(out32_Q14_ch0, A0_L_Q28)))
        next_S0 = smlawb(next_S0, out32_Q14_ch0, A0_U_Q28)
        next_S0 = smlawb(next_S0, B1_Q28, sample0)

        next_S1 = rshift_round_14(smulwb(out32_Q14_ch0, A1_L_Q28))
        next_S1 = smlawb(next_S1, out32_Q14_ch0, A1_U_Q28)
        next_S1 = smlawb(next_S1, B2_Q28, sample0)

        next_S2 = wrap32(S3 + rshift_round_14(smulwb(out32_Q14_ch1, A0_L_Q28)))
        next_S2 = smlawb(next_S2, out32_Q14_ch1, A0_U_Q28)
        next_S2 = smlawb(next_S2, B1_Q28, sample1)

        next_S3 = rshift_round_14(smulwb(out32_Q14_ch1, A1_L_Q28))
        next_S3 = smlawb(next_S3, out32_Q14_ch1, A1_U_Q28)
        next_S3 = smlawb(next_S3, B2_Q28, sample1)

        tmp0 = wrap32((to_signed(out32_Q14_ch0, 32) + 16383) >> 14)
        tmp1 = wrap32((to_signed(out32_Q14_ch1, 32) + 16383) >> 14)

        self.S0 = next_S0
        self.S1 = next_S1
        self.S2 = next_S2
        self.S3 = next_S3

        return {
            "out_sample0": to_unsigned(sat16(tmp0), 16),
            "out_sample1": to_unsigned(sat16(tmp1), 16),
        }
