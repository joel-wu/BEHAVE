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


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlSsmRwkvRwkv6WkvFwd:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_B,
        in_T,
        in_H,
        in_K,
        in_V,
        in_reverse,
        in_use_initial_state,
        in_output_final_state,
        in_scale,
        in_q,
        in_k,
        in_v,
        in_w,
        in_u,
        in_initial_state,
    ):
        legal = (
            in_B == 1
            and in_T == 1
            and in_H == 1
            and in_K == 1
            and in_V == 1
        )
        if not legal:
            return {"out_o": 0, "out_final_state": 0}

        state_in = in_initial_state if in_use_initial_state else 0
        kv = fp32_mul(in_k, in_v)
        bonus_kv = fp32_mul(kv, in_u)
        mix = fp32_add(state_in, bonus_kv)
        qs = fp32_mul(in_q, in_scale)
        out_o = fp32_mul(mix, qs)
        decay = exp_finite(in_w)
        decayed_state = fp32_mul(state_in, decay)
        state_out = fp32_add(decayed_state, kv)
        out_final_state = state_out if in_output_final_state else 0
        return {
            "out_o": to_unsigned(out_o, 32),
            "out_final_state": to_unsigned(out_final_state, 32),
        }
