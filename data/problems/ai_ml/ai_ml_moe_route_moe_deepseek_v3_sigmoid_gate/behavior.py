from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_cmp, fp32_neg, fp32_is_nan

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


def sigmoid_contract(x):
    a = exp_finite((x & 0x7FFFFFFF) | 0x80000000)
    denominator = fp32_add(0x3F800000, a)
    return fp32_div(a if x >> 31 else 0x3F800000, denominator)


def silu(x):
    return fp32_mul(x, sigmoid_contract(x))


NORM_EPS = 0x1E3CE508

def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def ranks_ahead(a_val, a_idx, b_val, b_idx):
    a_nan = fp32_is_nan(a_val)
    b_nan = fp32_is_nan(b_val)
    if a_nan and b_nan:
        return a_idx < b_idx
    if a_nan:
        return False
    if b_nan:
        return True
    c = fp32_cmp(a_val, b_val)
    if c > 0:
        return True
    if c < 0:
        return False
    return a_idx < b_idx


class AiMlMoeRouteMoeDeepseekV3SigmoidGate:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_router_logits_0,
        in_router_logits_1,
        in_router_logits_2,
        in_router_logits_3,
        in_router_logits_4,
        in_router_logits_5,
        in_router_logits_6,
        in_router_logits_7,
        in_e_score_correction_bias_0,
        in_e_score_correction_bias_1,
        in_e_score_correction_bias_2,
        in_e_score_correction_bias_3,
        in_e_score_correction_bias_4,
        in_e_score_correction_bias_5,
        in_e_score_correction_bias_6,
        in_e_score_correction_bias_7,
        in_norm_topk_prob,
        in_routed_scaling_factor,
    ):
        logits = [
            in_router_logits_0,
            in_router_logits_1,
            in_router_logits_2,
            in_router_logits_3,
            in_router_logits_4,
            in_router_logits_5,
            in_router_logits_6,
            in_router_logits_7,
        ]
        biases = [
            in_e_score_correction_bias_0,
            in_e_score_correction_bias_1,
            in_e_score_correction_bias_2,
            in_e_score_correction_bias_3,
            in_e_score_correction_bias_4,
            in_e_score_correction_bias_5,
            in_e_score_correction_bias_6,
            in_e_score_correction_bias_7,
        ]

        gate = [sigmoid_contract(x) for x in logits]
        choice = [fp32_add(gate[i], biases[i]) for i in range(8)]

        g0v0 = choice[0]
        g0v1 = choice[1]
        g0v2 = choice[2]
        g0v3 = choice[3]
        g0i0 = 0
        g0i1 = 1
        g0i2 = 2
        g0i3 = 3

        if ranks_ahead(g0v1, g0i1, g0v0, g0i0):
            g0v0, g0v1 = g0v1, g0v0
            g0i0, g0i1 = g0i1, g0i0
        if ranks_ahead(g0v2, g0i2, g0v0, g0i0):
            g0v0, g0v2 = g0v2, g0v0
            g0i0, g0i2 = g0i2, g0i0
        if ranks_ahead(g0v3, g0i3, g0v0, g0i0):
            g0v0, g0v3 = g0v3, g0v0
            g0i0, g0i3 = g0i3, g0i0
        if ranks_ahead(g0v2, g0i2, g0v1, g0i1):
            g0v1, g0v2 = g0v2, g0v1
            g0i1, g0i2 = g0i2, g0i1
        if ranks_ahead(g0v3, g0i3, g0v1, g0i1):
            g0v1, g0v3 = g0v3, g0v1
            g0i1, g0i3 = g0i3, g0i1
        if ranks_ahead(g0v3, g0i3, g0v2, g0i2):
            g0v2, g0v3 = g0v3, g0v2
            g0i2, g0i3 = g0i3, g0i2

        g1v0 = choice[4]
        g1v1 = choice[5]
        g1v2 = choice[6]
        g1v3 = choice[7]
        g1i0 = 4
        g1i1 = 5
        g1i2 = 6
        g1i3 = 7

        if ranks_ahead(g1v1, g1i1, g1v0, g1i0):
            g1v0, g1v1 = g1v1, g1v0
            g1i0, g1i1 = g1i1, g1i0
        if ranks_ahead(g1v2, g1i2, g1v0, g1i0):
            g1v0, g1v2 = g1v2, g1v0
            g1i0, g1i2 = g1i2, g1i0
        if ranks_ahead(g1v3, g1i3, g1v0, g1i0):
            g1v0, g1v3 = g1v3, g1v0
            g1i0, g1i3 = g1i3, g1i0
        if ranks_ahead(g1v2, g1i2, g1v1, g1i1):
            g1v1, g1v2 = g1v2, g1v1
            g1i1, g1i2 = g1i2, g1i1
        if ranks_ahead(g1v3, g1i3, g1v1, g1i1):
            g1v1, g1v3 = g1v3, g1v1
            g1i1, g1i3 = g1i3, g1i1
        if ranks_ahead(g1v3, g1i3, g1v2, g1i2):
            g1v2, g1v3 = g1v3, g1v2
            g1i2, g1i3 = g1i3, g1i2

        group_score_0 = fp32_add(g0v0, g0v1)
        group_score_1 = fp32_add(g1v0, g1v1)

        if ranks_ahead(group_score_0, 0, group_score_1, 1):
            sel0 = g0i0
            sel1 = g0i1
        else:
            sel0 = g1i0
            sel1 = g1i1

        w0_pre = gate[sel0]
        w1_pre = gate[sel1]

        if in_norm_topk_prob & 1:
            weight_sum = fp32_add(w0_pre, w1_pre)
            denominator = fp32_add(weight_sum, NORM_EPS)
            w0 = fp32_div(w0_pre, denominator)
            w1 = fp32_div(w1_pre, denominator)
        else:
            w0 = w0_pre
            w1 = w1_pre

        out_w0 = fp32_mul(w0, in_routed_scaling_factor)
        out_w1 = fp32_mul(w1, in_routed_scaling_factor)

        return {
            "out_topk_indices_0": to_unsigned(sel0, 3),
            "out_topk_indices_1": to_unsigned(sel1, 3),
            "out_topk_weights_0": to_unsigned(out_w0, 32),
            "out_topk_weights_1": to_unsigned(out_w1, 32),
        }
