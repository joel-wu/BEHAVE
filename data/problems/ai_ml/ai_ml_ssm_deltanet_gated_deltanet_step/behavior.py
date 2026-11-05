from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_neg, fp32_sqrt

EXP2_NODES = (1073741824, 1097253708, 1121280436, 1145833280, 1170923762, 1196563654, 1222764986, 1249540052, 1276901417, 1304861917, 1333434672, 1362633090, 1392470869, 1422962010, 1454120821, 1485961921, 1518500250, 1551751076, 1585730000, 1620452965, 1655936265, 1692196547, 1729250827, 1767116489, 1805811301, 1845353420, 1885761398, 1927054196, 1969251188, 2012372174, 2056437387, 2101467502, 2147483648)


def exp2_finite(x):
    magnitude = x & 0x7FFFFFFF
    exponent = (x >> 23) & 255
    if magnitude > 0x7F800000:
        return 0x7FC00000
    if magnitude == 0x7F800000:
        return 0 if x >> 31 else 0x7F800000
    if exponent == 0:
        return 0x3F800000
    if x >> 31 and magnitude > 0x42FC0000:
        return 0
    if not (x >> 31) and magnitude >= 0x43000000:
        return 0x7F800000
    # q=floor(65536*x), computed exactly from the binary32 fields.
    if exponent < 111:
        q = -1 if x >> 31 else 0
    else:
        shift = 134 - exponent
        mantissa = (x & 0x7FFFFF) | 0x800000
        q = -((mantissa + (1 << shift) - 1) >> shift) if x >> 31 else mantissa >> shift
    n = q // 65536
    fraction = q & 65535
    j = fraction >> 11
    r = fraction & 2047
    lo = EXP2_NODES[j]
    hi = EXP2_NODES[j + 1]
    y = lo + ((hi - lo) * r // 2048)
    significand = y >> 7
    remainder = y & 127
    if remainder > 64 or (remainder == 64 and significand & 1):
        significand = significand + 1
    if significand == 0x1000000:
        significand = significand >> 1
        n = n + 1
    if n > 127:
        return 0x7F800000
    return ((n + 127) << 23) | (significand & 0x7FFFFF)


def exp_finite(x):
    return exp2_finite(fp32_mul(x, 0x3FB8AA3B))


def softplus_contract(x):
    # log(1+e)=2*(w+w^3/3+...+w^11/11), w=e/(2+e), 0<=w<=1/3.
    e = exp_finite(x | 0x80000000)
    w = fp32_div(e, fp32_add(0x40000000, e))
    w2 = fp32_mul(w, w)
    term = w
    total = w
    for denominator in (3, 5, 7, 9, 11):
        term = fp32_mul(term, w2)
        divisor = 0x40400000 if denominator == 3 else 0x40A00000 if denominator == 5 else 0x40E00000 if denominator == 7 else 0x41100000 if denominator == 9 else 0x41300000
        total = fp32_add(total, fp32_div(term, divisor))
    correction = fp32_mul(0x40000000, total)
    return correction if x >> 31 else fp32_add(x, correction)


def dimension_word(n):
    exponent = 0
    while n >> (exponent + 1):
        exponent = exponent + 1
    if exponent <= 23:
        mantissa = n << (23 - exponent)
    else:
        shift = exponent - 23
        mantissa = n >> shift
        remainder = n - (mantissa << shift)
        midpoint = 1 << (shift - 1)
        if remainder > midpoint or (remainder == midpoint and mantissa & 1):
            mantissa = mantissa + 1
        if mantissa == 0x1000000:
            mantissa = mantissa >> 1
            exponent = exponent + 1
    return ((exponent + 127) << 23) | (mantissa & 0x7FFFFF)


def dot(left, right):
    total = fp32_mul(left[0], right[0])
    for i in range(1, len(left)):
        total = fp32_add(total, fp32_mul(left[i], right[i]))
    return total


def normalized(values):
    squared = dot(values, values)
    root = fp32_sqrt(fp32_add(squared, 0x358637BD))
    return [fp32_div(value, root) for value in values]


class AiMlSsmDeltanetGatedDeltanetStep:
    def __init__(self, K=8, V=8):
        self.K = K
        self.V = V
        self.query_scale = fp32_div(0x3F800000, fp32_sqrt(dimension_word(K)))
        self.reset()

    def reset(self):
        self.H = [0] * (self.K * self.V)
        self.A_log = 0
        self.dt_bias = 0

    def process(self, in_start, in_q, in_k, in_v, in_g, in_beta, in_A_log, in_dt_bias):
        if in_start:
            self.H = [0] * (self.K * self.V)
            self.A_log = in_A_log
            self.dt_bias = in_dt_bias
        q = normalized([(in_q >> (32 * i)) & 0xFFFFFFFF for i in range(self.K)])
        q = [fp32_mul(value, self.query_scale) for value in q]
        k = normalized([(in_k >> (32 * i)) & 0xFFFFFFFF for i in range(self.K)])
        v = [(in_v >> (32 * j)) & 0xFFFFFFFF for j in range(self.V)]
        dt = softplus_contract(fp32_add(in_g, self.dt_bias))
        log_decay = fp32_neg(fp32_mul(exp_finite(self.A_log), dt))
        decay = exp_finite(log_decay)
        decayed = [fp32_mul(value, decay) for value in self.H]
        next_state = [0] * (self.K * self.V)
        packed = 0
        for j in range(self.V):
            column = [decayed[i * self.V + j] for i in range(self.K)]
            residual = fp32_add(v[j], fp32_neg(dot(column, k)))
            delta = fp32_mul(in_beta, residual)
            updated = [fp32_add(column[i], fp32_mul(k[i], delta)) for i in range(self.K)]
            for i in range(self.K):
                next_state[i * self.V + j] = updated[i]
            packed = packed | (dot(updated, q) << (32 * j))
        self.H = next_state
        return {"out_o": packed}
