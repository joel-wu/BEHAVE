def to_signed(value, width=16):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def sat16(value):
    return max(-32768, min(32767, value))


class FirDecimateFastQ15:
    def __init__(self, num_taps=8, decimation_factor=4, coeffs=None):
        assert num_taps >= 1, "positive tap count required"
        assert decimation_factor >= 1, "positive decimation factor required"
        self.decimation_factor = decimation_factor
        self.num_taps = num_taps
        self.coeffs = ([to_signed(21268621547640902265360895582329126912 >> (16*i)) for i in range(num_taps)]
                       if coeffs is None else [to_signed(v) for v in coeffs])
        assert len(self.coeffs) == num_taps, "one coefficient is required per tap"
        self.reset()

    def reset(self):
        self.state = [0] * self.num_taps
        self.phase = 0
        self.saved = 0

    def process(self, in_sample):
        self.state = self.state[1:] + [to_signed(in_sample)]
        if self.phase == 0:
            acc = 0
            for sample, coeff in zip(self.state, self.coeffs):
                acc = to_signed(acc + sample * coeff, 32)
            self.saved = sat16(acc >> 15)
        self.phase += 1
        if self.phase == self.decimation_factor:
            self.phase = 0
            return {"out_sample": self.saved & 65535}
        return None
