def multiply_binary32(a, b):
    sign = ((a ^ b) >> 31) << 31
    ea = (a >> 23) & 255
    eb = (b >> 23) & 255
    fa = a & 8388607
    fb = b & 8388607
    if (ea == 255 and fa != 0) or (eb == 255 and fb != 0):
        return 4290772992
    if (ea == 255 and eb == 0 and fb == 0) or (eb == 255 and ea == 0 and fa == 0):
        return 4290772992
    if ea == 255 or eb == 255:
        return sign | 2139095040
    if (ea == 0 and fa == 0) or (eb == 0 and fb == 0):
        return sign
    ma = fa if ea == 0 else fa + 8388608
    mb = fb if eb == 0 else fb + 8388608
    xa = -126 if ea == 0 else ea - 127
    xb = -126 if eb == 0 else eb - 127
    product = ma * mb
    leading = 0
    for i in range(48):
        if product >> i:
            leading = i
    exponent = xa + xb - 46 + leading
    shift = leading - 23 if exponent >= -126 else -(xa + xb + 103)
    if shift <= 0:
        rounded = product << (-shift)
    elif shift > 48:
        rounded = 0
    else:
        rounded = product >> shift
        remainder = product - (rounded << shift)
        half = 1 << (shift - 1)
        if remainder > half or (remainder == half and (rounded & 1)):
            rounded += 1
    if exponent < -126:
        return sign | rounded
    if rounded >= 16777216:
        rounded >>= 1
        exponent += 1
    if exponent > 127:
        return sign | 2139095040
    return sign | ((exponent + 127) << 23) | (rounded & 8388607)


class FloatMultiplier:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = 0
        self.pending_result = 0
        self.z = 0

    def process(self, a, b):
        out = {"z": self.z}
        if self.phase == 0:
            self.pending_result = multiply_binary32(a, b)
        if self.phase == 6:
            self.z = self.pending_result
        self.phase = (self.phase + 1) & 7
        return out
