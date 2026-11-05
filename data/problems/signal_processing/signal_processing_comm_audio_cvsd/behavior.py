def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


ONE_Q = 268435456
NEG_ONE_Q = -268435456
DELTA_MIN_Q = 2684354
ALPHA_Q = 241591910
APLUSB_Q = 507343011
APRODB_Q = 239175991


def qmul_q28(a, b):
    return (a * b) >> 28


def div_trunc_toward_zero_3(v):
    if v < 0:
        return -((-v) // 3)
    return v // 3


def clamp_ref_q28(v):
    if v > ONE_Q:
        return ONE_Q
    if v < NEG_ONE_Q:
        return NEG_ONE_Q
    return v


def clamp_delta_q28(v):
    if v > ONE_Q:
        return ONE_Q
    if v < DELTA_MIN_Q:
        return DELTA_MIN_Q
    return v


def pcm_u8_to_q28(v):
    return to_signed(v, 8) << 21


def q28_to_pcm_u8(v):
    if v < 0:
        q = -((-v) >> 21)
    else:
        q = v >> 21
    if q > 127:
        q = 127
    elif q < -128:
        q = -128
    return to_unsigned(q, 8)


class SignalProcessingCommAudioCvsd:
    def __init__(self):
        self.reset()

    def reset(self):
        self.bitref = 0
        self.ref = 0
        self.delta = DELTA_MIN_Q
        self.prefilt_x1 = 0
        self.post_x1 = 0
        self.post_y1 = 0
        self.post_y2 = 0

    def process(self, in_mode, in_data):
        if (in_mode & 1) == 0:
            x_q = pcm_u8_to_q28(in_data)
            y_q = x_q - qmul_q28(ALPHA_Q, self.prefilt_x1)
            bit = 0 if self.ref > y_q else 1
        else:
            x_q = self.prefilt_x1
            bit = in_data & 1

        bitref = ((self.bitref << 1) | bit) & 7
        if bitref == 0 or bitref == 7:
            delta = self.delta + (self.delta >> 1)
        else:
            delta = div_trunc_toward_zero_3(self.delta * 2)
        delta = clamp_delta_q28(delta)

        if bit:
            ref = self.ref + delta
        else:
            ref = self.ref - delta
        ref = clamp_ref_q28(ref)

        self.bitref = bitref
        self.ref = ref
        self.delta = delta
        if (in_mode & 1) == 0:
            self.prefilt_x1 = x_q
            return {"out_data": bit}

        y_q = (ref - self.post_x1
               + qmul_q28(APLUSB_Q, self.post_y1)
               - qmul_q28(APRODB_Q, self.post_y2))
        self.post_x1 = ref
        self.post_y2 = self.post_y1
        self.post_y1 = y_q
        return {"out_data": q28_to_pcm_u8(y_q)}
