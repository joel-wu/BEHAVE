from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_cmp, fp32_neg, fp32_sqrt

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


def to_unsigned(v, b):
    return v & ((1 << b) - 1)

def fp32_word(x):
    return x & 0xFFFFFFFF

def fp32_sqrt_defined(a):
    return fp32_sqrt(a)

def unpack_vec(x, n):
    x = to_unsigned(x, 32 * n)
    return [(x >> (32 * i)) & 0xFFFFFFFF for i in range(n)]

def pack_vec(xs):
    y = 0
    for i, v in enumerate(xs):
        y |= to_unsigned(v, 32) << (32 * i)
    return y

def matvec4_rowmajor(w, v):
    out = []
    for r in range(4):
        p0 = fp32_mul(fp32_word(w[4 * r + 0]), fp32_word(v[0]))
        p1 = fp32_mul(fp32_word(w[4 * r + 1]), fp32_word(v[1]))
        p2 = fp32_mul(fp32_word(w[4 * r + 2]), fp32_word(v[2]))
        p3 = fp32_mul(fp32_word(w[4 * r + 3]), fp32_word(v[3]))
        s01 = fp32_add(p0, p1)
        s23 = fp32_add(p2, p3)
        out.append(fp32_add(s01, s23))
    return out

class AiMlTxAttnCrossT5CrossAttention:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_hidden_states,
        in_key_value_states,
        in_attention_mask,
        in_ln_weight,
        in_q_weight,
        in_k_weight,
        in_v_weight,
        in_o_weight,
    ):
        x = unpack_vec(in_hidden_states, 4)
        enc_flat = unpack_vec(in_key_value_states, 16)
        enc = [enc_flat[4 * t:4 * t + 4] for t in range(4)]
        mask = unpack_vec(in_attention_mask, 4)
        gamma = unpack_vec(in_ln_weight, 4)
        wq = unpack_vec(in_q_weight, 16)
        wk = unpack_vec(in_k_weight, 16)
        wv = unpack_vec(in_v_weight, 16)
        wo = unpack_vec(in_o_weight, 16)

        sq0 = fp32_mul(fp32_word(x[0]), fp32_word(x[0]))
        sq1 = fp32_mul(fp32_word(x[1]), fp32_word(x[1]))
        sq2 = fp32_mul(fp32_word(x[2]), fp32_word(x[2]))
        sq3 = fp32_mul(fp32_word(x[3]), fp32_word(x[3]))
        s01 = fp32_add(sq0, sq1)
        s23 = fp32_add(sq2, sq3)
        sumsq = fp32_add(s01, s23)
        mean = fp32_div(sumsq, 0x40800000)
        den = fp32_add(mean, 0x358637BD)
        sqrt_den = fp32_sqrt_defined(den)
        scale = fp32_div(0x3F800000, sqrt_den)

        norm_pre = [fp32_mul(fp32_word(x[d]), fp32_word(scale)) for d in range(4)]
        norm = [fp32_mul(fp32_word(gamma[d]), fp32_word(norm_pre[d])) for d in range(4)]

        q_full = matvec4_rowmajor(wq, norm)
        k_full = [matvec4_rowmajor(wk, enc[t]) for t in range(4)]
        v_full = [matvec4_rowmajor(wv, enc[t]) for t in range(4)]

        probs = []
        for h in range(2):
            base = 2 * h
            scores = []
            for t in range(4):
                dot0 = fp32_mul(fp32_word(q_full[base + 0]), fp32_word(k_full[t][base + 0]))
                dot1 = fp32_mul(fp32_word(q_full[base + 1]), fp32_word(k_full[t][base + 1]))
                dot = fp32_add(dot0, dot1)
                scores.append(fp32_add(dot, fp32_word(mask[t])))

            probs.append(softmax(scores))

        ctx_full = []
        for h in range(2):
            base = 2 * h
            for c in range(2):
                t0 = fp32_mul(fp32_word(probs[h][0]), fp32_word(v_full[0][base + c])) if probs[h][0] & 0x7FFFFFFF else 0
                t1 = fp32_mul(fp32_word(probs[h][1]), fp32_word(v_full[1][base + c])) if probs[h][1] & 0x7FFFFFFF else 0
                t2 = fp32_mul(fp32_word(probs[h][2]), fp32_word(v_full[2][base + c])) if probs[h][2] & 0x7FFFFFFF else 0
                t3 = fp32_mul(fp32_word(probs[h][3]), fp32_word(v_full[3][base + c])) if probs[h][3] & 0x7FFFFFFF else 0
                cs01 = fp32_add(t0, t1)
                cs23 = fp32_add(t2, t3)
                ctx_full.append(fp32_add(cs01, cs23))

        proj = matvec4_rowmajor(wo, ctx_full)
        layer_output = [fp32_add(fp32_word(x[d]), fp32_word(proj[d])) for d in range(4)]

        return {
            "out_layer_output": pack_vec(layer_output),
            "out_position_bias": pack_vec(mask),
            "out_attn_weights": pack_vec(probs[0]),
        }
