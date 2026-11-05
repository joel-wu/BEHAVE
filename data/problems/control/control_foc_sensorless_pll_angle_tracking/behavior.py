def signed16(word):
    return ((word & 65535) ^ 32768) - 32768


def trunc_shift(value, bits):
    return -((-value) >> bits) if value < 0 else value >> bits


def sat32(value):
    return min(2147483647, max(-2147483648, value))


def boost(value):
    scaled = -(11 * (-value) // 10) if value < 0 else 11 * value // 10
    return sat32(scaled)


def angle(y, x):
    ax = abs(x)
    ay = abs(y)
    base = 0
    if ax >= ay and ax != 0:
        base = (ay << 29) // ax
    elif ay != 0:
        base = 1073741824 - (ax << 29) // ay
    if x < 0:
        base = 2147483648 - base if y >= 0 else 2147483648 + base
    elif y < 0:
        base = 4294967296 - base
    return base & 4294967295


class OrtegaPLL:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x1 = 0
        self.x2 = 0
        self.phase = 0
        self.speed = 0

    def process(self, in_v_alpha, in_v_beta, in_i_alpha, in_i_beta,
                in_dt, in_r, in_l, in_lambda, in_gamma_half, in_pll_kp, in_pll_ki):
        va = signed16(in_v_alpha) << 9
        vb = signed16(in_v_beta) << 9
        ia = signed16(in_i_alpha)
        ib = signed16(in_i_beta)
        dt = signed16(in_dt)
        resistance = signed16(in_r)
        inductance = signed16(in_l)
        linkage = signed16(in_lambda) << 9
        gain = signed16(in_gamma_half)
        kp = signed16(in_pll_kp)
        ki = signed16(in_pll_ki)
        lia = trunc_shift(inductance * ia, 6)
        lib = trunc_shift(inductance * ib, 6)
        ria = trunc_shift(resistance * ia, 6)
        rib = trunc_shift(resistance * ib, 6)
        fa = self.x1 - lia
        fb = self.x2 - lib
        error = min(0, trunc_shift(linkage * linkage - fa * fa - fb * fb, 24))
        ca = trunc_shift(gain * trunc_shift(fa * error, 24), 15)
        cb = trunc_shift(gain * trunc_shift(fb * error, 24), 15)
        a = sat32(self.x1 + trunc_shift((va - ria + ca) * dt, 15))
        b = sat32(self.x2 + trunc_shift((vb - rib + cb) * dt, 15))
        if linkage > 0 and 4 * (a * a + b * b) < linkage * linkage:
            a = boost(a)
            b = boost(b)
        observed = angle(b - lib, a - lia)
        delta = ((observed - self.phase + 2147483648) & 4294967295) - 2147483648
        kp_term = trunc_shift(kp * delta, 23)
        ki_term = trunc_shift(ki * delta, 23)
        phase = (self.phase + trunc_shift((self.speed + kp_term) * dt, 7)) & 4294967295
        speed = sat32(self.speed + trunc_shift(ki_term * dt, 15))
        self.x1 = a
        self.x2 = b
        self.phase = phase
        self.speed = speed
        return {"out_obs_phase": observed, "out_pll_phase": phase,
                "out_pll_speed": speed & 4294967295,
                "out_x1": a & 4294967295, "out_x2": b & 4294967295}
