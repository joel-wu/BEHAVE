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

from fp_lib import fp32_sub

POS_ZERO = 0

def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ChunkedGatedLinearAttention:
    def __init__(self, B=1, T=1, H=1, K=1, V=1, BT=16):
        self.B = B
        self.T = T
        self.H = H
        self.K = K
        self.V = V
        self.BT = BT
        self.reset()

    def reset(self):
        pass

    def process(self, in_scale, in_output_final_state, in_q, in_k, in_v, in_g, in_initial_state):
        B, T, H, K, V, BT = self.B, self.T, self.H, self.K, self.V, self.BT
        n_qkv = B * T * H * K
        n_v = B * T * H * V
        n_state = B * H * K * V
        scale = to_unsigned(in_scale, 32)

        q = [to_unsigned((in_q >> (32 * i)) & 0xFFFFFFFF, 32) for i in range(n_qkv)]
        k = [to_unsigned((in_k >> (32 * i)) & 0xFFFFFFFF, 32) for i in range(n_qkv)]
        g = [to_unsigned((in_g >> (32 * i)) & 0xFFFFFFFF, 32) for i in range(n_qkv)]
        v = [to_unsigned((in_v >> (32 * i)) & 0xFFFFFFFF, 32) for i in range(n_v)]
        state = [to_unsigned((in_initial_state >> (32 * i)) & 0xFFFFFFFF, 32) for i in range(n_state)]

        def idx_q(b, t, h, d):
            return (((b * T) + t) * H + h) * K + d

        def idx_v(b, t, h, e):
            return (((b * T) + t) * H + h) * V + e

        def idx_state(b, h, d, e):
            return (((b * H) + h) * K + d) * V + e

        # Step 1: chunk-local cumulative sum of g, FP32 add fold from +0.0
        g_cumsum = [POS_ZERO] * n_qkv
        NT = (T + BT - 1) // BT

        for b in range(B):
            for h in range(H):
                for c in range(NT):
                    ts = c * BT
                    te = T if (c + 1) * BT > T else (c + 1) * BT
                    for d in range(K):
                        s = POS_ZERO
                        for t in range(ts, te):
                            s = fp32_add(s, g[idx_q(b, t, h, d)])
                            g_cumsum[idx_q(b, t, h, d)] = s

        out_o_bits = [POS_ZERO] * n_v

        for b in range(B):
            for h in range(H):
                for c in range(NT):
                    ts = c * BT
                    te = T if (c + 1) * BT > T else (c + 1) * BT
                    t_last = te - 1

                    chunk_entry_state = [POS_ZERO] * (K * V)
                    for d in range(K):
                        for e in range(V):
                            chunk_entry_state[d * V + e] = state[idx_state(b, h, d, e)]

                    for t in range(ts, te):
                        for e in range(V):
                            s_inter = POS_ZERO
                            for d in range(K):
                                term = fp32_mul(q[idx_q(b, t, h, d)],
                                                exp_finite(g_cumsum[idx_q(b, t, h, d)]))
                                term = fp32_mul(term, chunk_entry_state[d * V + e])
                                s_inter = fp32_add(s_inter, term)
                            inter_val = fp32_mul(s_inter, scale)

                            s_intra = POS_ZERO
                            for u in range(ts, t + 1):
                                a = POS_ZERO
                                for d in range(K):
                                    prod = fp32_mul(q[idx_q(b, t, h, d)],
                                                    k[idx_q(b, u, h, d)])
                                    gate = exp_finite(
                                        fp32_sub(g_cumsum[idx_q(b, t, h, d)],
                                                 g_cumsum[idx_q(b, u, h, d)]))
                                    a = fp32_add(a, fp32_mul(prod, gate))
                                A_val = fp32_mul(a, scale)
                                term = fp32_mul(A_val, v[idx_v(b, u, h, e)])
                                s_intra = fp32_add(s_intra, term)

                            out_o_bits[idx_v(b, t, h, e)] = fp32_add(inter_val, s_intra)

                    for d in range(K):
                        g_end = g_cumsum[idx_q(b, t_last, h, d)]
                        for e in range(V):
                            s = fp32_mul(state[idx_state(b, h, d, e)], exp_finite(g_end))
                            for u in range(ts, te):
                                gdiff = fp32_sub(g_end, g_cumsum[idx_q(b, u, h, d)])
                                exp_val = exp_finite(gdiff)
                                kv = fp32_mul(k[idx_q(b, u, h, d)],
                                              v[idx_v(b, u, h, e)])
                                term = fp32_mul(exp_val, kv)
                                s = fp32_add(s, term)
                            state[idx_state(b, h, d, e)] = s

        out_o = 0
        for i, val in enumerate(out_o_bits):
            out_o |= to_unsigned(val, 32) << (32 * i)

        out_final_state = 0
        if in_output_final_state & 1:
            for i, val in enumerate(state):
                out_final_state |= to_unsigned(val, 32) << (32 * i)

        return {
            "out_o": to_unsigned(out_o, n_v * 32),
            "out_final_state": to_unsigned(out_final_state, n_state * 32),
        }
