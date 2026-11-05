def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class Q15VectorScaleElement:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_sample = 0

    def process(self, in_sample, in_scale_fract, in_shift):
        sample = to_signed(in_sample, 16)
        scale_fract = to_signed(in_scale_fract, 16)
        shift = to_signed(in_shift, 8)
        product = sample * scale_fract
        k_shift = 15 - shift
        if k_shift >= 0:
            shifted = product >> k_shift
        else:
            shifted = product << (-k_shift)
        if shifted > 32767:
            result = 32767
        elif shifted < -32768:
            result = -32768
        else:
            result = shifted
        self.out_sample = to_unsigned(result, 16)
        return {"out_sample": self.out_sample}