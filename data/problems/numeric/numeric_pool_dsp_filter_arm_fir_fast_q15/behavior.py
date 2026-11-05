def to_signed(value, width=16):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def sat16(value):
    return max(-32768, min(32767, value))


class Q15FastFir:
    def __init__(self, num_taps=8, coeffs=None):
        assert num_taps >= 2 and num_taps % 2 == 0, "positive even tap count required"
        self.num_taps = num_taps
        self.coeffs = ([to_signed(21268621547640902265360895582329126912 >> (16*i)) for i in range(num_taps)]
                       if coeffs is None else [to_signed(v) for v in coeffs])
        assert len(self.coeffs) == num_taps, "one coefficient is required per tap"
        self.reset()

    def reset(self):
        self.state = [0] * self.num_taps

    def process(self, in_sample):
        self.state = self.state[1:] + [to_signed(in_sample)]
        acc = 0
        for sample, coeff in zip(self.state, self.coeffs):
            acc = to_signed(acc + sample * coeff, 32)
        return {"out_sample": sat16(acc >> 15) & 65535}
