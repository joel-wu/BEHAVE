from fp_lib import fp32_add, fp32_sub, fp32_mul

Q48 = 1 << 48
LN2 = 195103586505167

def logarithm_q32(x):
    # x is a positive normal binary32; nine odd terms of 2*atanh(w).
    e = ((x >> 23) & 255) - 127
    m = Q48 + ((x & 0x7fffff) << 25)
    w = ((m - Q48) << 48) // (m + Q48)
    square = (w*w) >> 48
    power, total = w, 0
    for j in range(9):
        total += power // (2*j+1)
        power = (power*square) >> 48
    return (e*LN2 + 2*total) >> 16

def bandwidth_q32(x):
    e = (x >> 23) & 255
    if e == 0:
        return 0
    shift = e - 118
    m = (1 << 23) + (x & 0x7fffff)
    return m << shift if shift >= 0 else m >> -shift

def exponential_q32(z):
    # Range reduction and 18 Taylor terms, with integer Q48 arithmetic.
    z48 = z << 16
    k = z48 // LN2
    r = z48 - k*LN2
    term, total = Q48, Q48
    for j in range(1, 19):
        term = ((term*r) >> 48) // j
        total += term
    mantissa = total >> 25
    tail = total & ((1 << 25)-1)
    if tail > (1 << 24) or (tail == (1 << 24) and (mantissa & 1)):
        mantissa += 1
    if mantissa >= (1 << 24):
        mantissa >>= 1
        k += 1
    return ((k+127) << 23) | (mantissa & 0x7fffff)

def next_gain(gain, energy, alpha):
    if energy <= 0x358637bd:  # binary32 1e-6
        return gain
    numerator = -bandwidth_q32(alpha)*logarithm_q32(energy)
    z = abs(numerator) // (1 << 33)
    if numerator < 0:
        z = -z
    result = fp32_mul(gain, exponential_q32(z))
    return min(result, 0x49742400)  # positive gain, ceiling 1e6


class SignalProcessingCommLoopAgcRrrf:
    def __init__(self):
        self.reset()

    def reset(self):
        self.gain = 0x3f800000
        self.energy = 0x3f800000

    def process(self, in_x):
        alpha = 0x3c23d70a
        if alpha == 0x80000000:
            alpha = 0
        invalid = alpha > 0x3f800000 or ((in_x >> 23) & 255) == 255
        values = [fp32_mul(in_x, self.gain)]
        power = fp32_mul(values[0], values[0])
        energy = fp32_add(fp32_mul(fp32_sub(0x3f800000, alpha), self.energy), fp32_mul(alpha, power))
        invalid = invalid or ((energy >> 23) & 255) == 255
        if invalid:
            return {"out_y": 0, "out_gain": self.gain, "out_error": 1}
        self.energy = energy
        self.gain = next_gain(self.gain, energy, alpha)
        return {"out_y": values[0], "out_gain": self.gain, "out_error": 0}
