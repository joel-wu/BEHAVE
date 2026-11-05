from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_cmp, fp32_neg

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


def is_nan(x):
    return (x & 0x7FFFFFFF) > 0x7F800000


def softmax(scores):
    count = len(scores)
    weights = [0] * count
    maximum = 0xFF800000
    bad = False
    for i in range(count):
        if is_nan(scores[i]):
            bad = True
        elif fp32_cmp(scores[i], maximum) > 0:
            maximum = scores[i]
    if bad:
        return [0x7FC00000] * count
    if maximum == 0xFF800000:
        return weights
    total = 0
    for i in range(count):
        if maximum == 0x7F800000:
            weights[i] = 0x3F800000 if scores[i] == maximum else 0
        else:
            weights[i] = exp_finite(fp32_add(scores[i], fp32_neg(maximum)))
        total = fp32_add(total, weights[i])
    for i in range(count):
        weights[i] = fp32_div(weights[i], total)
    return weights


class GraniteSdpaAttention:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_query, in_key, in_value, in_cos, in_sin, in_attention_mask, in_scaling):
        query = [(in_query >> (32 * i)) & 0xFFFFFFFF for i in range(8)]
        key = [(in_key >> (32 * i)) & 0xFFFFFFFF for i in range(8)]
        value = [(in_value >> (32 * i)) & 0xFFFFFFFF for i in range(8)]
        cos = [(in_cos >> (32 * d)) & 0xFFFFFFFF for d in range(4)]
        sin = [(in_sin >> (32 * d)) & 0xFFFFFFFF for d in range(4)]
        output = 0
        packed_weights = 0
        for h in range(2):
            rq = [0] * 4
            for d in range(4):
                rotated = query[4 * h + (d + 2) % 4]
                if d < 2:
                    rotated = fp32_neg(rotated)
                rq[d] = fp32_add(fp32_mul(query[4 * h + d], cos[d]), fp32_mul(rotated, sin[d]))
            scores = [0] * 2
            for t in range(2):
                rk = [0] * 4
                for d in range(4):
                    rotated = key[4 * t + (d + 2) % 4]
                    if d < 2:
                        rotated = fp32_neg(rotated)
                    rk[d] = fp32_add(fp32_mul(key[4 * t + d], cos[d]), fp32_mul(rotated, sin[d]))
                acc = fp32_mul(rq[0], rk[0])
                for d in range(1, 4):
                    acc = fp32_add(acc, fp32_mul(rq[d], rk[d]))
                mask = (in_attention_mask >> (32 * (2 * h + t))) & 0xFFFFFFFF
                scores[t] = fp32_add(fp32_mul(acc, in_scaling), mask)
            weights = softmax(scores)
            for t in range(2):
                packed_weights = packed_weights | (weights[t] << (32 * (2 * h + t)))
            for d in range(4):
                acc = 0
                for t in range(2):
                    if weights[t] & 0x7FFFFFFF:
                        acc = fp32_add(acc, fp32_mul(weights[t], value[4 * t + d]))
                output = output | (acc << (32 * (4 * h + d)))
        return {'out_attn_output': output, 'out_attn_weights': packed_weights}
