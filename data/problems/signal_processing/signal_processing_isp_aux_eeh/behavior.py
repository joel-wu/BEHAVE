def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero_pow2(v, shift):
    if v >= 0:
        return v >> shift
    return -((-v) >> shift)


class SignalProcessingIspAuxEeh:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_p00, in_p01, in_p02, in_p03, in_p04,
        in_p10, in_p11, in_p12, in_p13, in_p14,
        in_p20, in_p21, in_p22, in_p23, in_p24,
        in_f00, in_f01, in_f02, in_f03, in_f04,
        in_f10, in_f11, in_f12, in_f13, in_f14,
        in_f20, in_f21, in_f22, in_f23, in_f24,
        in_gain0, in_gain1, in_thres0, in_thres1, in_emclip0, in_emclip1
    ):
        p00 = to_unsigned(in_p00, 8)
        p01 = to_unsigned(in_p01, 8)
        p02 = to_unsigned(in_p02, 8)
        p03 = to_unsigned(in_p03, 8)
        p04 = to_unsigned(in_p04, 8)
        p10 = to_unsigned(in_p10, 8)
        p11 = to_unsigned(in_p11, 8)
        p12 = to_unsigned(in_p12, 8)
        p13 = to_unsigned(in_p13, 8)
        p14 = to_unsigned(in_p14, 8)
        p20 = to_unsigned(in_p20, 8)
        p21 = to_unsigned(in_p21, 8)
        p22 = to_unsigned(in_p22, 8)
        p23 = to_unsigned(in_p23, 8)
        p24 = to_unsigned(in_p24, 8)

        f00 = to_signed(in_f00, 8)
        f01 = to_signed(in_f01, 8)
        f02 = to_signed(in_f02, 8)
        f03 = to_signed(in_f03, 8)
        f04 = to_signed(in_f04, 8)
        f10 = to_signed(in_f10, 8)
        f11 = to_signed(in_f11, 8)
        f12 = to_signed(in_f12, 8)
        f13 = to_signed(in_f13, 8)
        f14 = to_signed(in_f14, 8)
        f20 = to_signed(in_f20, 8)
        f21 = to_signed(in_f21, 8)
        f22 = to_signed(in_f22, 8)
        f23 = to_signed(in_f23, 8)
        f24 = to_signed(in_f24, 8)

        gain0 = to_signed(in_gain0, 16)
        gain1 = to_signed(in_gain1, 16)
        thres0 = to_signed(in_thres0, 16)
        thres1 = to_signed(in_thres1, 16)
        emclip0 = to_signed(in_emclip0, 16)
        emclip1 = to_signed(in_emclip1, 16)

        edge_sum = (
            p00 * f00 + p01 * f01 + p02 * f02 + p03 * f03 + p04 * f04 +
            p10 * f10 + p11 * f11 + p12 * f12 + p13 * f13 + p14 * f14 +
            p20 * f20 + p21 * f21 + p22 * f22 + p23 * f23 + p24 * f24
        )
        em_value = trunc_div_toward_zero_pow2(edge_sum, 3)

        if em_value < -thres1:
            lut_preclip = gain1 * em_value
        elif em_value < -thres0 and em_value > -thres1:
            lut_preclip = 0
        elif em_value < thres0 and em_value > -thres1:
            lut_preclip = gain0 * em_value
        elif em_value > thres0 and em_value < thres1:
            lut_preclip = 0
        elif em_value > thres1:
            lut_preclip = gain1 * em_value
        else:
            lut_preclip = 0

        # Keep the correction in Q8 through clipping and center addition.
        correction_q8 = max(emclip0*256, min(lut_preclip, emclip1*256))
        ee_pixel = min(255, max(0, (p12*256 + correction_q8)//256))
        return {
            "out_ee_pixel": ee_pixel,
            "out_em_value": to_unsigned(em_value, 17),
        }
