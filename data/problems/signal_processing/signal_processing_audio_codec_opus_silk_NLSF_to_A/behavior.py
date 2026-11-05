# Derived from Xiph Opus v1.5.2 SILK; see accompanying SOURCE_LICENSE.
# Exact integer specialization: QA=16, scalar C inverse-gain arithmetic.
COS_Q12 = [8192, 8190, 8182, 8170, 8152, 8130, 8104, 8072, 8034, 7994, 7946, 7896, 7840, 7778, 7714, 7644, 7568, 7490, 7406, 7318, 7226, 7128, 7026, 6922, 6812, 6698, 6580, 6458, 6332, 6204, 6070, 5934, 5792, 5648, 5502, 5352, 5198, 5040, 4880, 4718, 4552, 4382, 4212, 4038, 3862, 3684, 3502, 3320, 3136, 2948, 2760, 2570, 2378, 2186, 1990, 1794, 1598, 1400, 1202, 1002, 802, 602, 402, 202, 0, -202, -402, -602, -802, -1002, -1202, -1400, -1598, -1794, -1990, -2186, -2378, -2570, -2760, -2948, -3136, -3320, -3502, -3684, -3862, -4038, -4212, -4382, -4552, -4718, -4880, -5040, -5198, -5352, -5502, -5648, -5792, -5934, -6070, -6204, -6332, -6458, -6580, -6698, -6812, -6922, -7026, -7128, -7226, -7318, -7406, -7490, -7568, -7644, -7714, -7778, -7840, -7896, -7946, -7994, -8034, -8072, -8104, -8130, -8152, -8170, -8182, -8190, -8192]
ORDER10 = [0, 9, 6, 3, 4, 5, 8, 1, 2, 7]
ORDER16 = [0, 15, 8, 7, 4, 11, 12, 3, 2, 13, 10, 5, 6, 9, 14, 1]

def s32(x):
    return ((x + 2147483648) & 4294967295) - 2147483648

def sat32(x):
    return max(-2147483648, min(2147483647, x))

def rounded(x, shift):
    return (x + (1 << (shift - 1))) >> shift

def polynomial(c, offset, order):
    p = [0] * 9
    p[0] = 65536
    p[1] = -c[offset]
    for k in range(1, 8):
        if k < order // 2:
            f = c[2*k+offset]
            p[k+1] = s32(2*p[k-1] - rounded(f*p[k], 16))
            for j in range(7):
                n = k-j
                if n > 1:
                    p[n] = s32(p[n]+p[n-2]-rounded(f*p[n-1], 16))
            p[1] = s32(p[1]-f)
    return p

def bandwidth(a, order, chirp):
    out = [0]*16
    delta = chirp-65536
    for k in range(16):
        if k < order:
            out[k] = s32((a[k]*chirp) >> 16)
            chirp += rounded(s32(chirp*delta), 16)
    return out

def quantize(a, order, clip):
    out = [0]*16
    for k in range(16):
        if k < order:
            value = rounded(a[k], 5)
            if clip != 0:
                value = max(-32768, min(32767, value))
            out[k] = ((value+32768) & 65535)-32768
    return out

def rescale(a, order):
    out = [0]*16
    for k in range(16):
        if k < order:
            out[k] = a[k]*32
    return out

def reciprocal(b):
    # SILK INVERSE32_varQ(b, bit_length(b)+30); the final shift is zero.
    bits = 0
    for j in range(31):
        if b >= (1 << j):
            bits = j+1
    normal = b << (31-bits)
    inv = 536870911 // (normal >> 16)
    residual = (536870912-((normal*inv) >> 16)) << 3
    return sat32((inv << 16)+((residual*inv) >> 16)), bits

def inverse_gain(a, order):
    work = [0]*16
    dc = 0
    for j in range(16):
        if j < order:
            dc += a[j]
            work[j] = a[j] << 12
    valid = dc < 4096
    gain = 1073741824
    for step in range(16):
        k = order-1-step
        if valid and k >= 0:
            if abs(work[k]) > 16773022:
                valid = False
            else:
                rc = -work[k]*128
                factor = 1073741824-((rc*rc) >> 32)
                gain = ((gain*factor) >> 32)*4
                if gain < 107374:
                    valid = False
                elif k > 0:
                    inverse, shift = reciprocal(factor)
                    for n in range(8):
                        if n < (k+1)//2:
                            left = work[n]
                            right = work[k-n-1]
                            l = rounded(sat32(left-rounded(right*rc, 31))*inverse, shift)
                            r = rounded(sat32(right-rounded(left*rc, 31))*inverse, shift)
                            if l < -2147483648 or l > 2147483647 or r < -2147483648 or r > 2147483647:
                                valid = False
                            work[n] = s32(l)
                            work[k-n-1] = s32(r)
    if not valid:
        gain = 0
    return gain

class SilkNLSFToA:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_d, in_NLSF_0, in_NLSF_1, in_NLSF_2, in_NLSF_3, in_NLSF_4, in_NLSF_5, in_NLSF_6, in_NLSF_7, in_NLSF_8, in_NLSF_9, in_NLSF_10, in_NLSF_11, in_NLSF_12, in_NLSF_13, in_NLSF_14, in_NLSF_15):
        order = in_d & 31
        words = [in_NLSF_0, in_NLSF_1, in_NLSF_2, in_NLSF_3, in_NLSF_4, in_NLSF_5, in_NLSF_6, in_NLSF_7, in_NLSF_8, in_NLSF_9, in_NLSF_10, in_NLSF_11, in_NLSF_12, in_NLSF_13, in_NLSF_14, in_NLSF_15]
        a = [0]*16
        q = [0]*16
        status = 1
        if order == 10 or order == 16:
            status = 0
            c = [0]*16
            for k in range(16):
                if k < order:
                    x = words[k] & 32767
                    i = x >> 8
                    v = rounded(COS_Q12[i]*256+(COS_Q12[i+1]-COS_Q12[i])*(x & 255), 4)
                    index = ORDER16[k]
                    if order == 10:
                        index = ORDER10[k]
                    c[index] = v
            p = polynomial(c, 0, order)
            r = polynomial(c, 1, order)
            for k in range(8):
                if k < order//2:
                    ps = s32(p[k+1]+p[k])
                    qs = s32(r[k+1]-r[k])
                    a[k] = s32(-qs-ps)
                    a[order-k-1] = s32(qs-ps)
            fitted = False
            for attempt in range(10):
                if not fitted:
                    peak = 0
                    index = 0
                    for k in range(16):
                        if k < order and abs(a[k]) > peak:
                            peak = abs(a[k])
                            index = k
                    peak = rounded(peak, 5)
                    if peak <= 32767:
                        fitted = True
                    else:
                        peak = min(peak, 163838)
                        chirp = 65470-(((peak-32767) << 14)//((peak*(index+1)) >> 2))
                        a = bandwidth(a, order, chirp)
            q = quantize(a, order, 1)
            if not fitted:
                a = rescale(q, order)
            stable = False
            for attempt in range(16):
                if not stable:
                    stable = inverse_gain(q, order) != 0
                    if not stable:
                        a = bandwidth(a, order, 65536-(2 << attempt))
                        q = quantize(a, order, 0)
        return {
            "out_status": status,
            "out_a_Q12_0": q[0] & 65535,
            "out_a_Q12_1": q[1] & 65535,
            "out_a_Q12_2": q[2] & 65535,
            "out_a_Q12_3": q[3] & 65535,
            "out_a_Q12_4": q[4] & 65535,
            "out_a_Q12_5": q[5] & 65535,
            "out_a_Q12_6": q[6] & 65535,
            "out_a_Q12_7": q[7] & 65535,
            "out_a_Q12_8": q[8] & 65535,
            "out_a_Q12_9": q[9] & 65535,
            "out_a_Q12_10": q[10] & 65535,
            "out_a_Q12_11": q[11] & 65535,
            "out_a_Q12_12": q[12] & 65535,
            "out_a_Q12_13": q[13] & 65535,
            "out_a_Q12_14": q[14] & 65535,
            "out_a_Q12_15": q[15] & 65535
        }
