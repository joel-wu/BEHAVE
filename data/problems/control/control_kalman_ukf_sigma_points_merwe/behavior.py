def signed(word, bits):
    value = word & ((1 << bits) - 1)
    return value - (1 << bits) if value >= (1 << (bits - 1)) else value


def tz(numerator, denominator):
    return -((-numerator) // denominator) if numerator < 0 else numerator // denominator


def root84(n):
    result = 0
    bit = 1 << 82
    for j in range(42):
        if n >= result + bit:
            n = n - result - bit
            result = (result >> 1) + bit
        else:
            result = result >> 1
        bit = bit >> 2
    return result


class MerweSigmaPoints:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_p00, in_p01, in_p10, in_p11,
                in_alpha, in_beta, in_kappa):
        x0 = signed(in_x0, 32) << 16
        x1 = signed(in_x1, 32) << 16
        p00 = signed(in_p00, 32)
        p01 = signed(in_p01, 32)
        p10 = signed(in_p10, 32)
        p11 = signed(in_p11, 32)
        alpha = in_alpha & 65535
        beta = signed(in_beta, 16)
        kappa = signed(in_kappa, 16)
        determinant = p00 * p11 - p01 * p01
        error = int(alpha == 0 or kappa < -256 or kappa > 1024 or
                    beta < 0 or beta > 1024 or p00 <= 0 or p11 <= 0 or
                    p01 != p10 or determinant <= 0)
        u00 = 0
        u01 = 0
        u11 = 0
        wm0 = 0
        wc0 = 0
        other = 0
        if not error:
            d = alpha * alpha * (512 + kappa)
            u00 = root84(d * p00 * 256)
            magnitude = root84((d * p01 * p01 * 256) // p00)
            u01 = -magnitude if p01 < 0 else magnitude
            u11 = root84((d * determinant * 256) // p00)
            wm0 = tz((d - 2199023255552) * 16777216, d)
            other = 9223372036854775808 // d
            wc0 = tz((2*d - 2199023255552) * 4294967296 - d*alpha*alpha + d*beta*16777216, d*256)
        else:
            x0 = 0
            x1 = 0
        return {
            "out_sigma0_0": (x0) & 18446744073709551615,
            "out_sigma0_1": (x1) & 18446744073709551615,
            "out_sigma1_0": (x0+u00) & 18446744073709551615,
            "out_sigma1_1": (x1+u01) & 18446744073709551615,
            "out_sigma2_0": (x0) & 18446744073709551615,
            "out_sigma2_1": (x1+u11) & 18446744073709551615,
            "out_sigma3_0": (x0-u00) & 18446744073709551615,
            "out_sigma3_1": (x1-u01) & 18446744073709551615,
            "out_sigma4_0": (x0) & 18446744073709551615,
            "out_sigma4_1": (x1-u11) & 18446744073709551615,
            "out_wm0": (wm0) & 18446744073709551615,
            "out_wm1": (other) & 18446744073709551615,
            "out_wm2": (other) & 18446744073709551615,
            "out_wm3": (other) & 18446744073709551615,
            "out_wm4": (other) & 18446744073709551615,
            "out_wc0": (wc0) & 18446744073709551615,
            "out_wc1": (other) & 18446744073709551615,
            "out_wc2": (other) & 18446744073709551615,
            "out_wc3": (other) & 18446744073709551615,
            "out_wc4": (other) & 18446744073709551615,
            "out_error": error}
