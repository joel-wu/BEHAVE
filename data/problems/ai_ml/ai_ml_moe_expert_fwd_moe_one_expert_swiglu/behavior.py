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


def sigmoid_contract(x):
    a = exp_finite((x & 0x7FFFFFFF) | 0x80000000)
    denominator = fp32_add(0x3F800000, a)
    return fp32_div(a if x >> 31 else 0x3F800000, denominator)


def silu(x):
    return fp32_mul(x, sigmoid_contract(x))


class AiMlMoeExpertFwdMoeOneExpertSwiglu:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x_0, in_x_1, in_gate_w_0_0, in_gate_w_0_1, in_gate_w_1_0, in_gate_w_1_1, in_up_w_0_0, in_up_w_0_1, in_up_w_1_0, in_up_w_1_1, in_down_w_0_0, in_down_w_0_1, in_down_w_1_0, in_down_w_1_1, in_top_k_weight):
        gate0 = fp32_add(fp32_mul(in_x_0, in_gate_w_0_0), fp32_mul(in_x_1, in_gate_w_0_1))
        gate1 = fp32_add(fp32_mul(in_x_0, in_gate_w_1_0), fp32_mul(in_x_1, in_gate_w_1_1))
        up0 = fp32_add(fp32_mul(in_x_0, in_up_w_0_0), fp32_mul(in_x_1, in_up_w_0_1))
        up1 = fp32_add(fp32_mul(in_x_0, in_up_w_1_0), fp32_mul(in_x_1, in_up_w_1_1))
        h0 = fp32_mul(silu(gate0), up0)
        h1 = fp32_mul(silu(gate1), up1)
        y0 = fp32_add(fp32_mul(h0, in_down_w_0_0), fp32_mul(h1, in_down_w_0_1))
        y1 = fp32_add(fp32_mul(h0, in_down_w_1_0), fp32_mul(h1, in_down_w_1_1))
        return {"out_y_0": fp32_mul(y0, in_top_k_weight),
                "out_y_1": fp32_mul(y1, in_top_k_weight)}
