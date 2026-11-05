def s16(x):
    x &= 65535
    return x - 65536 if x >= 32768 else x

def nearest(n, d):
    if d == 0:
        return 0
    q = (abs(n) + abs(d) // 2) // abs(d)
    return -q if (n < 0) != (d < 0) else q

def root(n):
    r = 0
    for b in range(31, -1, -1):
        t = r | (1 << b)
        if t * t <= n:
            r = t
    return r

def prod(ar, ai, br, bi):
    return (nearest(ar * br, 16384) - nearest(ai * bi, 16384),
            nearest(ar * bi, 16384) + nearest(ai * br, 16384))

def reflector(ar, ai, xr, xi):
    if xr == 0 and xi == 0 and ai == 0:
        return ar, 0, 0, 0, 0
    norm = root(ar * ar + ai * ai + xr * xr + xi * xi)
    beta = -norm if ar >= 0 else norm
    tr = nearest((beta - ar) * 16384, beta)
    ti = nearest(-ai * 16384, beta)
    den = (ar - beta) * (ar - beta) + ai * ai
    vr = nearest((xr * (ar - beta) + xi * ai) * 16384, den)
    vi = nearest((xi * (ar - beta) - xr * ai) * 16384, den)
    return beta, tr, ti, vr, vi

def encode(value, divisor):
    x = nearest(value, divisor)
    if x > 32767:
        x = 32767
    elif x < -32768:
        x = -32768
    return x & 65535

class ComplexQr2x2Householder:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a00_re, in_a00_im, in_a01_re, in_a01_im,
                in_a10_re, in_a10_im, in_a11_re, in_a11_im):
        ar = s16(in_a00_re) * 16384
        ai = s16(in_a00_im) * 16384
        xr = s16(in_a10_re) * 16384
        xi = s16(in_a10_im) * 16384
        br = s16(in_a01_re) * 16384
        bi = s16(in_a01_im) * 16384
        cr = s16(in_a11_re) * 16384
        ci = s16(in_a11_im) * 16384
        beta, tr, ti, vr, vi = reflector(ar, ai, xr, xi)
        pr, pi = prod(vr, -vi, cr, ci)
        wr = br + pr
        wi = bi + pi
        sr, si = prod(tr, -ti, wr, wi)
        r01r = br - sr
        r01i = bi - si
        pr, pi = prod(vr, vi, sr, si)
        r11r = cr - pr
        r11i = ci - pi
        beta1, tr1, ti1, unused_r, unused_i = reflector(r11r, r11i, 0, 0)
        return {
            "out_r00_re": encode(beta, 16384), "out_r00_im": 0,
            "out_r01_re": encode(r01r, 16384), "out_r01_im": encode(r01i, 16384),
            "out_r10_re": 0, "out_r10_im": 0,
            "out_r11_re": encode(beta1, 16384), "out_r11_im": 0,
            "out_tau0_re": encode(tr, 2), "out_tau0_im": encode(ti, 2),
            "out_tau1_re": encode(tr1, 2), "out_tau1_im": encode(ti1, 2),
            "out_v10_re": encode(vr, 2), "out_v10_im": encode(vi, 2),
        }
