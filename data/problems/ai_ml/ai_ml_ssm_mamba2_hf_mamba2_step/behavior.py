def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def wrap32(v):
    return to_signed(v, 32)


def qmul(a, b):
    return wrap32((to_signed(a, 32) * to_signed(b, 32)) >> 16)


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


class AiMlSsmMamba2HfMamba2Step:
    def __init__(self):
        self.reset()

    def reset(self):
        self.conv_state = [[0, 0, 0] for _ in range(6)]
        self.recurrent_state = [[0, 0], [0, 0]]

    def process(
        self,
        in_gate_0,
        in_gate_1,
        in_hidden_bc_0,
        in_hidden_bc_1,
        in_hidden_bc_2,
        in_hidden_bc_3,
        in_hidden_bc_4,
        in_hidden_bc_5,
        in_dt_0,
    ):
        conv_state = [[self.conv_state[ch][0], self.conv_state[ch][1], self.conv_state[ch][2]] for ch in range(6)]
        recurrent_state = [
            [self.recurrent_state[0][0], self.recurrent_state[0][1]],
            [self.recurrent_state[1][0], self.recurrent_state[1][1]],
        ]

        inputs = [
            to_signed(in_hidden_bc_0, 32),
            to_signed(in_hidden_bc_1, 32),
            to_signed(in_hidden_bc_2, 32),
            to_signed(in_hidden_bc_3, 32),
            to_signed(in_hidden_bc_4, 32),
            to_signed(in_hidden_bc_5, 32),
        ]

        taps = []
        for ch in range(6):
            taps.append([conv_state[ch][1], conv_state[ch][2], inputs[ch]])

        coeffs = [
            [0x00004000, 0x00008000, 0x00010000],
            [0x00002000, 0x00006000, 0x0000C000],
            [0x00008000, 0x00004000, 0x00002000],
            [-0x00004000, 0x00004000, 0x00008000],
            [0x00006000, 0x00002000, -0x00002000],
            [0x00004000, -0x00004000, 0x00004000],
        ]

        conv_sum = []
        for ch in range(6):
            s = wrap32(qmul(taps[ch][0], coeffs[ch][0]) + qmul(taps[ch][1], coeffs[ch][1]) + qmul(taps[ch][2], coeffs[ch][2]))
            conv_sum.append(s)

        act = [silu16(conv_sum[i]) for i in range(6)]

        hidden_0 = act[0]
        hidden_1 = act[1]
        b_raw_0 = act[2]
        b_raw_1 = act[3]
        c_raw_0 = act[4]
        c_raw_1 = act[5]

        dt_pre = wrap32(to_signed(in_dt_0, 32) + 0x00008000)
        dt_softplus = softplus16(dt_pre)
        dt_times_neg1 = qmul(dt_softplus, -0x00010000)
        d_a_0 = exp16(dt_times_neg1)
        d_a_1 = d_a_0

        d_b_00 = qmul(dt_softplus, b_raw_0)
        d_b_01 = qmul(dt_softplus, b_raw_1)
        d_b_10 = d_b_00
        d_b_11 = d_b_01

        d_bx_00 = qmul(d_b_00, hidden_0)
        d_bx_01 = qmul(d_b_01, hidden_0)
        d_bx_10 = qmul(d_b_10, hidden_1)
        d_bx_11 = qmul(d_b_11, hidden_1)

        new_state_00 = wrap32(qmul(recurrent_state[0][0], d_a_0) + d_bx_00)
        new_state_01 = wrap32(qmul(recurrent_state[0][1], d_a_0) + d_bx_01)
        new_state_10 = wrap32(qmul(recurrent_state[1][0], d_a_1) + d_bx_10)
        new_state_11 = wrap32(qmul(recurrent_state[1][1], d_a_1) + d_bx_11)

        y_ssm_0 = wrap32(qmul(new_state_00, c_raw_0) + qmul(new_state_01, c_raw_1))
        y_ssm_1 = wrap32(qmul(new_state_10, c_raw_0) + qmul(new_state_11, c_raw_1))

        y_pre_norm_0 = wrap32(y_ssm_0 + qmul(hidden_0, 0x00010000))
        y_pre_norm_1 = wrap32(y_ssm_1 + qmul(hidden_1, 0x00010000))

        gate_act_0 = silu16(s32(in_gate_0))
        gate_act_1 = silu16(s32(in_gate_1))

        gated_0 = qmul(y_pre_norm_0, gate_act_0)
        gated_1 = qmul(y_pre_norm_1, gate_act_1)

        denominator = (gated_0 * gated_0 + gated_1 * gated_1) // 2 + 4295
        normed_0 = normalize(gated_0, denominator)
        normed_1 = normalize(gated_1, denominator)

        out_y_0 = qmul(0x00010000, normed_0)
        out_y_1 = qmul(0x00010000, normed_1)

        for ch in range(6):
            self.conv_state[ch] = [taps[ch][0], taps[ch][1], taps[ch][2]]
        self.recurrent_state[0][0] = new_state_00
        self.recurrent_state[0][1] = new_state_01
        self.recurrent_state[1][0] = new_state_10
        self.recurrent_state[1][1] = new_state_11

        return {
            "out_y_0": to_unsigned(out_y_0, 32),
            "out_y_1": to_unsigned(out_y_1, 32),
        }
