S = 65536
Q = 1099511627776
LN2 = 762123384786


def s32(value):
    value &= 0xFFFFFFFF
    return value - 4294967296 if value >= 2147483648 else value


def sat(value):
    return max(-2147483648, min(2147483647, value))


def rne(numerator, denominator):
    negative = numerator < 0
    magnitude = -numerator if negative else numerator
    quotient = magnitude // denominator
    remainder = magnitude - quotient * denominator
    if 2 * remainder > denominator or (2 * remainder == denominator and quotient & 1):
        quotient += 1
    return -quotient if negative else quotient


def exp40(value):
    if value <= -32 * S:
        return 0
    # Callers bound positive arguments below 11; negative arguments may be any Q16.16 value.
    wide = value * 16777216
    exponent = wide // LN2
    residual = wide - exponent * LN2
    term = Q
    total = Q
    for degree in range(1, 13):
        term = rne(term * residual, Q * degree)
        total += term
    if exponent >= 0:
        return total << exponent
    return rne(total, 1 << (-exponent))


def exp16(value):
    if value >= 11 * S:
        return 2147483647
    if value <= -16 * S:
        return 0
    return sat(rne(exp40(value), 16777216))


def softplus16(value):
    magnitude = -value if value < 0 else value
    small = exp40(-magnitude)
    z = rne(small * Q, 2 * Q + small)
    square = rne(z * z, Q)
    term = z
    total = z
    for index in range(1, 12):
        term = rne(term * square, Q)
        total += rne(term, 2 * index + 1)
    correction = rne(2 * total, 16777216)
    return sat(max(value, 0) + correction)


def silu16(value):
    magnitude = -value if value < 0 else value
    small = exp40(-magnitude)
    numerator = magnitude * (small if value < 0 else Q)
    result = rne(numerator, Q + small)
    return -result if value < 0 else result


def norm_root(denominator):
    target = denominator << 32
    root = 0
    for bit in range(47, -1, -1):
        trial = root | (1 << bit)
        if trial * trial <= target:
            root = trial
    return root


def normalize(value, denominator):
    return rne(value * 4294967296, norm_root(denominator))


def sigmoid16(value):
    magnitude = -value if value < 0 else value
    tail = exp40(-magnitude)
    return rne(S * (tail if value < 0 else Q), Q + tail)

from behave_intrinsics import swrap, uwrap


Q16_ONE = 0x00010000
Q16_MONE = -0x00010000


class Qwen3NextGatedDeltaNet:
    def __init__(self):
        self.reset()

    def reset(self):
        self.conv_state_query = 0
        self.conv_state_key = 0
        self.conv_state_value = 0
        self.recurrent_state = 0

    def q16_mul(self, x, y):
        prod = x * y
        if prod >= 0:
            q = prod >> 16
        else:
            q = -(((-prod) >> 16))
        return swrap(q, 32)

    def process(
        self,
        in_query,
        in_key,
        in_value,
        in_b,
        in_a,
        in_z,
        in_conv_weight_q_prev,
        in_conv_weight_q_cur,
        in_conv_weight_k_prev,
        in_conv_weight_k_cur,
        in_conv_weight_v_prev,
        in_conv_weight_v_cur,
        in_dt_bias,
        in_A,
    ):
        conv_state_query = self.conv_state_query
        conv_state_key = self.conv_state_key
        conv_state_value = self.conv_state_value
        recurrent_state = self.recurrent_state

        q_conv_acc = swrap(
            self.q16_mul(conv_state_query, swrap(in_conv_weight_q_prev, 32))
            + self.q16_mul(swrap(in_query, 32), swrap(in_conv_weight_q_cur, 32)),
            32,
        )
        k_conv_acc = swrap(
            self.q16_mul(conv_state_key, swrap(in_conv_weight_k_prev, 32))
            + self.q16_mul(swrap(in_key, 32), swrap(in_conv_weight_k_cur, 32)),
            32,
        )
        v_conv_acc = swrap(
            self.q16_mul(conv_state_value, swrap(in_conv_weight_v_prev, 32))
            + self.q16_mul(swrap(in_value, 32), swrap(in_conv_weight_v_cur, 32)),
            32,
        )

        q_conv = silu16(q_conv_acc)
        k_conv = silu16(k_conv_acc)
        v_conv = silu16(v_conv_acc)

        query_scaled = normalize(q_conv, q_conv * q_conv + 4295)
        key_unit = normalize(k_conv, k_conv * k_conv + 4295)

        beta = sigmoid16(swrap(in_b, 32))
        dt = softplus16(swrap(swrap(in_a, 32) + swrap(in_dt_bias, 32), 32))
        g = swrap(-self.q16_mul(swrap(in_A, 32), dt), 32)
        decay = exp16(g)

        state_decayed = self.q16_mul(recurrent_state, decay)
        kv_mem = self.q16_mul(state_decayed, key_unit)
        delta = self.q16_mul(swrap(v_conv - kv_mem, 32), beta)
        state_add = self.q16_mul(key_unit, delta)
        new_recurrent_state = swrap(state_decayed + state_add, 32)

        core_attn = self.q16_mul(new_recurrent_state, query_scaled)

        normed = normalize(core_attn, core_attn * core_attn + 4295)

        z_silu = silu16(swrap(in_z, 32))
        out_data = self.q16_mul(normed, z_silu)

        self.conv_state_query = swrap(in_query, 32)
        self.conv_state_key = swrap(in_key, 32)
        self.conv_state_value = swrap(in_value, 32)
        self.recurrent_state = new_recurrent_state

        return {"out_data": uwrap(out_data, 32)}
