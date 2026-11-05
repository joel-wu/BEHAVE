def signed32(word):
    value = word & 4294967295
    return value - 4294967296 if value >= 2147483648 else value


def rne(n, d):
    magnitude = -n if n < 0 else n
    q = magnitude // d
    r = magnitude % d
    if 2*r > d or (2*r == d and q % 2 == 1):
        q += 1
    return -q if n < 0 else q


def root80(n):
    result = 0
    bit = 1 << 78
    for j in range(40):
        if n >= result + bit:
            n = n - result - bit
            result = (result >> 1) + bit
        else:
            result = result >> 1
        bit = bit >> 2
    return result


def sigma(x0, x1, p00, p01, p11):
    det = p00*p11-p01*p01
    a = root80(p00 << 47)
    b = root80((p01*p01 << 47) // p00)
    if p01 < 0:
        b = -b
    c = root80((det << 47) // p00)
    m0 = x0 << 16
    m1 = x1 << 16
    return [m0, m1, m0+a, m1+b, m0, m1+c, m0-a, m1-b, m0, m1-c]


def in32(value):
    return -2147483648 <= value and value <= 2147483647


def valid_state(x0, x1, p00, p01, p11):
    return (in32(x0) and in32(x1) and in32(p00) and in32(p01) and in32(p11)
            and p00 > 0 and p11 > 0 and p00*p11-p01*p01 > 0)


class ControlKalmanUkfFullPredictUpdate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x0 = 0
        self.x1 = 0
        self.p00 = 32768
        self.p01 = 0
        self.p11 = 32768

    def process(self, in_measurement, in_dt):
        z = signed32(in_measurement)
        dt = in_dt & 65535
        points = sigma(self.x0, self.x1, self.p00, self.p01, self.p11)
        propagated = [0] * 10
        sum0 = 0
        sum1 = 0
        for i in range(5):
            propagated[2*i] = points[2*i] + rne(dt*points[2*i+1], 65536)
            propagated[2*i+1] = points[2*i+1]
            w = -3 if i == 0 else 1
            sum0 += w*propagated[2*i]
            sum1 += w*propagated[2*i+1]
        xp0 = rne(sum0, 65536)
        xp1 = rne(sum1, 65536)
        c00 = 0
        c01 = 0
        c11 = 0
        for i in range(5):
            a = propagated[2*i] - (xp0 << 16)
            b = propagated[2*i+1] - (xp1 << 16)
            w4 = -1 if i == 0 else 4
            c00 += w4*a*a
            c01 += w4*a*b
            c11 += w4*b*b
        pp00 = rne(c00, 1125899906842624) + 512
        pp01 = rne(c01, 1125899906842624)
        pp11 = rne(c11, 1125899906842624) + 512
        error = 1
        packed_x = 0
        packed_p = 0
        packed_gain = 0
        innovation = 0
        variance = 0
        if valid_state(xp0, xp1, pp00, pp01, pp11):
            # Measurement points include process noise through the new prior.
            prior = sigma(xp0, xp1, pp00, pp01, pp11)
            zsum = 0
            for i in range(5):
                w = -3 if i == 0 else 1
                zsum += w*prior[2*i]
            zp = rne(zsum, 65536)
            ss = 0
            cs0 = 0
            cs1 = 0
            for i in range(5):
                dx0 = prior[2*i] - (xp0 << 16)
                dx1 = prior[2*i+1] - (xp1 << 16)
                dz = prior[2*i] - (zp << 16)
                w4 = -1 if i == 0 else 4
                ss += w4*dz*dz
                cs0 += w4*dx0*dz
                cs1 += w4*dx1*dz
            s = rne(ss, 1125899906842624) + 512
            pxz0 = rne(cs0, 1125899906842624)
            pxz1 = rne(cs1, 1125899906842624)
            if s > 0 and s <= 2147483647:
                k0 = rne(pxz0 << 16, s)
                k1 = rne(pxz1 << 16, s)
                y = z - zp
                nx0 = rne((xp0 << 16) + k0*y, 65536)
                nx1 = rne((xp1 << 16) + k1*y, 65536)
                np00 = rne((pp00 << 32) - k0*k0*s, 4294967296)
                np01 = rne((pp01 << 32) - k0*k1*s, 4294967296)
                np11 = rne((pp11 << 32) - k1*k1*s, 4294967296)
                if valid_state(nx0, nx1, np00, np01, np11) and in32(k0) and in32(k1):
                    self.x0 = nx0
                    self.x1 = nx1
                    self.p00 = np00
                    self.p01 = np01
                    self.p11 = np11
                    error = 0
                    packed_x = (nx0 & 4294967295) | ((nx1 & 4294967295) << 32)
                    packed_p = ((np00 & 4294967295) | ((np01 & 4294967295) << 32)
                                | ((np11 & 4294967295) << 64))
                    packed_gain = (k0 & 4294967295) | ((k1 & 4294967295) << 32)
                    innovation = y & 18446744073709551615
                    variance = s
        return {"out_x": packed_x, "out_p": packed_p, "out_gain": packed_gain,
                "out_innovation": innovation, "out_variance": variance, "out_error": error}
