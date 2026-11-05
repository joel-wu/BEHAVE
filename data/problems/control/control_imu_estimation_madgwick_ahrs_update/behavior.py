def signed16(v):
    v = v & 65535
    return v - 65536 if v >= 32768 else v


def clip(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def mul(a, b):
    # Wide Q15 multiplication, not a saturating 16-bit multiply.
    return (a * b) >> 15


def root96(n):
    # Restoring integer square root: exactly floor(sqrt(n)), 0 <= n < 2**96.
    root = 0
    remainder = 0
    for k in range(48):
        remainder = (remainder << 2) | ((n >> (94 - 2*k)) & 3)
        trial = (root << 2) | 1
        root = root << 1
        if remainder >= trial:
            remainder = remainder - trial
            root = root | 1
    return root


def norm_den(a, b, c, d):
    return root96((a*a + b*b + c*c + d*d) << 32)


def unit(v, denominator):
    if denominator == 0:
        return 0
    numerator = v << 31
    quotient = numerator // denominator if numerator >= 0 else -((-numerator) // denominator)
    return clip(quotient, -32768, 32767)


class ControlImuEstimationMadgwickAhrsUpdate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q0 = 32767
        self.q1 = 0
        self.q2 = 0
        self.q3 = 0
        self.accMagP = 32768

    def process(self, in_gx, in_gy, in_gz, in_ax, in_ay, in_az, in_dt, in_beta, in_acc_confidence_decay):
        q0 = self.q0
        q1 = self.q1
        q2 = self.q2
        q3 = self.q3
        accMagP = self.accMagP
        gx = signed16(in_gx)
        gy = signed16(in_gy)
        gz = signed16(in_gz)
        ax = signed16(in_ax)
        ay = signed16(in_ay)
        az = signed16(in_az)
        dt = signed16(in_dt)
        beta = signed16(in_beta)
        acc_confidence_decay = signed16(in_acc_confidence_decay)
        d0 = (-mul(q1,gx) - mul(q2,gy) - mul(q3,gz)) >> 1
        d1 = (mul(q0,gx) + mul(q2,gz) - mul(q3,gy)) >> 1
        d2 = (mul(q0,gy) - mul(q1,gz) + mul(q3,gx)) >> 1
        d3 = (mul(q0,gz) + mul(q1,gy) - mul(q2,gx)) >> 1
        acc2 = ax*ax + ay*ay + az*az
        if acc2 > 107374:
            accMagP = (9*accMagP + root96(acc2)) // 10
            delta = accMagP - 32768
            delta = -delta if delta < 0 else delta
            confidence = clip(32768 - mul(acc_confidence_decay,root96(delta << 15)),0,32768)
            den = norm_den(ax,ay,az,0)
            a = unit(ax,den)
            b = unit(ay,den)
            c = unit(az,den)
            q00 = mul(q0,q0)
            q11 = mul(q1,q1)
            q22 = mul(q2,q2)
            q33 = mul(q3,q3)
            s0 = mul(4*q0,q22) + mul(2*q2,a) + mul(4*q0,q11) - mul(2*q1,b)
            s1 = mul(4*q1,q33) - mul(2*q3,a) + mul(4*q00,q1) - mul(2*q0,b) - 4*q1 + mul(8*q1,q11) + mul(8*q1,q22) + mul(4*q1,c)
            s2 = mul(4*q00,q2) + mul(2*q0,a) + mul(4*q2,q33) - mul(2*q3,b) - 4*q2 + mul(8*q2,q11) + mul(8*q2,q22) + mul(4*q2,c)
            s3 = mul(4*q11,q3) - mul(2*q1,a) + mul(4*q22,q3) - mul(2*q2,b)
            den = norm_den(s0,s1,s2,s3)
            bc = mul(beta,confidence)
            d0 = d0 - mul(bc,unit(s0,den))
            d1 = d1 - mul(bc,unit(s1,den))
            d2 = d2 - mul(bc,unit(s2,den))
            d3 = d3 - mul(bc,unit(s3,den))
        n0 = q0 + mul(d0,dt)
        n1 = q1 + mul(d1,dt)
        n2 = q2 + mul(d2,dt)
        n3 = q3 + mul(d3,dt)
        den = norm_den(n0,n1,n2,n3)
        q0 = unit(n0,den) if den != 0 else 32767
        q1 = unit(n1,den)
        q2 = unit(n2,den)
        q3 = unit(n3,den)
        self.q0 = q0
        self.q1 = q1
        self.q2 = q2
        self.q3 = q3
        self.accMagP = accMagP
        return {
            "out_q0": q0 & 65535,
            "out_q1": q1 & 65535,
            "out_q2": q2 & 65535,
            "out_q3": q3 & 65535,
            "out_accMagP": accMagP & 65535
        }
