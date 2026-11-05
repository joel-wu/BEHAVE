def to_unsigned(value, width):
    return value & ((1 << width) - 1)


def to_signed(value, width):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def sat16(value):
    return 32767 if value > 32767 else -32768 if value < -32768 else value


# CMSIS-DSP armRecipTableQ15, Apache-2.0; source identity is in the repair audit.
RECIP_TABLE = (
    0x7F03, 0x7D13, 0x7B31, 0x795E, 0x7798, 0x75E0, 0x7434, 0x7294,
    0x70FF, 0x6F76, 0x6DF6, 0x6C82, 0x6B16, 0x69B5, 0x685C, 0x670C,
    0x65C4, 0x6484, 0x634C, 0x621C, 0x60F3, 0x5FD0, 0x5EB5, 0x5DA0,
    0x5C91, 0x5B88, 0x5A85, 0x5988, 0x5890, 0x579E, 0x56B0, 0x55C8,
    0x54E4, 0x5405, 0x532B, 0x5255, 0x5183, 0x50B6, 0x4FEC, 0x4F26,
    0x4E64, 0x4DA6, 0x4CEC, 0x4C34, 0x4B81, 0x4AD0, 0x4A23, 0x4978,
    0x48D1, 0x482D, 0x478C, 0x46ED, 0x4651, 0x45B8, 0x4521, 0x448D,
    0x43FC, 0x436C, 0x42DF, 0x4255, 0x41CC, 0x4146, 0x40C2, 0x4040,
)


def normalized_weight(error_mu, denominator):
    # The upstream reciprocal requires a positive Q15 input. The full-port task
    # freezes learning when the narrowed energy-plus-delta is nonpositive.
    if denominator <= 0:
        return 0
    normalized = denominator
    shift = 0
    for _ in range(14):
        if normalized < 16384:
            normalized <<= 1
            shift += 1
    estimate = RECIP_TABLE[(normalized >> 8) & 63]
    for _ in range(2):
        residual = 32767 - ((normalized * estimate) >> 15)
        estimate = to_signed((estimate * residual) >> 14, 16)
    return sat16((error_mu * estimate) >> (14 - shift))


class Q15NormalizedLmsFilterStep:
    def __init__(self):
        self.reset()

    def reset(self):
        self.coeffs = [0] * 8
        self.state = [0] * 7
        self.energy = 0
        self.x0 = 0

    def process(self, in_sample, in_ref, in_mu, in_postShift):
        sample = to_signed(in_sample, 16)
        desired = to_signed(in_ref, 16)
        mu = to_signed(in_mu, 16)
        post_shift = to_unsigned(in_postShift, 5)
        window = self.state + [sample]
        energy = sat16(self.energy - ((self.x0 * self.x0) >> 15)
                       + ((sample * sample) >> 15))
        mac = 0
        for i in range(8):
            mac += window[i] * self.coeffs[i]
        if post_shift <= 15:
            output = sat16(mac >> (15 - post_shift))
        else:
            output = sat16(mac << (post_shift - 15))
        error = to_signed(desired - output, 16)
        error_mu = to_signed((error * mu) >> 15, 16)
        denominator = to_signed(energy + 5, 16)
        weight = normalized_weight(error_mu, denominator)
        coefficients = [0] * 8
        for i in range(8):
            coefficients[i] = sat16(self.coeffs[i] + ((weight * window[i]) >> 15))
        self.coeffs = coefficients
        self.energy = energy
        self.x0 = self.state[0]
        self.state = window[1:]
        return {"out_sample": to_unsigned(output,16), "out_error": to_unsigned(error,16)}
