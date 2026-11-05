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


class MahonyImuQ15Integral:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q0 = 32767
        self.q1 = 0
        self.q2 = 0
        self.q3 = 0
        self.accMagP = 32768
        self.integralFBx = 0
        self.integralFBy = 0
        self.integralFBz = 0

    def process(self, in_gx, in_gy, in_gz, in_ax, in_ay, in_az, in_dt, in_kp, in_ki, in_acc_confidence_decay):
        q0 = self.q0
        q1 = self.q1
        q2 = self.q2
        q3 = self.q3
        accMagP = self.accMagP
        integralFBx = self.integralFBx
        integralFBy = self.integralFBy
        integralFBz = self.integralFBz
        gx = signed16(in_gx)
        gy = signed16(in_gy)
        gz = signed16(in_gz)
        ax = signed16(in_ax)
        ay = signed16(in_ay)
        az = signed16(in_az)
        dt = signed16(in_dt)
        kp = signed16(in_kp)
        ki = signed16(in_ki)
        acc_confidence_decay = signed16(in_acc_confidence_decay)
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
            vx = mul(q1,q3) - mul(q0,q2)
            vy = mul(q0,q1) + mul(q2,q3)
            vz = mul(q0,q0) - 16384 + mul(q3,q3)
            ex = mul(b,vz) - mul(c,vy)
            ey = mul(c,vx) - mul(a,vz)
            ez = mul(a,vy) - mul(b,vx)
            pk = mul(2*kp,confidence)
            ik = mul(2*ki,confidence)
            if ik > 0:
                integralFBx = clip(integralFBx + ((ik*ex*dt) >> 30),-32768,32767)
                integralFBy = clip(integralFBy + ((ik*ey*dt) >> 30),-32768,32767)
                integralFBz = clip(integralFBz + ((ik*ez*dt) >> 30),-32768,32767)
            else:
                integralFBx = 0
                integralFBy = 0
                integralFBz = 0
            gx = gx + integralFBx + mul(pk,ex)
            gy = gy + integralFBy + mul(pk,ey)
            gz = gz + integralFBz + mul(pk,ez)
        hx = (gx*dt) >> 16
        hy = (gy*dt) >> 16
        hz = (gz*dt) >> 16
        n0 = q0 - mul(q1,hx) - mul(q2,hy) - mul(q3,hz)
        n1 = q1 + mul(q0,hx) + mul(q2,hz) - mul(q3,hy)
        n2 = q2 + mul(q0,hy) - mul(q1,hz) + mul(q3,hx)
        n3 = q3 + mul(q0,hz) + mul(q1,hy) - mul(q2,hx)
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
        self.integralFBx = integralFBx
        self.integralFBy = integralFBy
        self.integralFBz = integralFBz
        return {
            "out_q0": q0 & 65535,
            "out_q1": q1 & 65535,
            "out_q2": q2 & 65535,
            "out_q3": q3 & 65535
        }
