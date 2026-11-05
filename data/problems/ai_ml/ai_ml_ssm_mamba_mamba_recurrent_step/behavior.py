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


def silu_approx(x):
    return silu16(s32(x))


def softplus_approx(x):
    return softplus16(s32(x))


def exp_approx(x):
    return exp16(s32(x))


class ai_ml_ssm_mamba_mamba_recurrent_step:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_hidden_state,
        in_conv_state_0,
        in_conv_state_1,
        in_conv_state_2,
        in_conv_state_3,
        in_ssm_state_0,
        in_ssm_state_1,
        in_ssm_state_2,
        in_ssm_state_3,
        in_ssm_state_4,
        in_ssm_state_5,
        in_ssm_state_6,
        in_ssm_state_7,
        in_ssm_state_8,
        in_ssm_state_9,
        in_ssm_state_10,
        in_ssm_state_11,
        in_ssm_state_12,
        in_ssm_state_13,
        in_ssm_state_14,
        in_ssm_state_15,
        in_proj_weight_x,
        in_proj_weight_z,
        in_proj_bias_x,
        in_proj_bias_z,
        conv1d_weight_0,
        conv1d_weight_1,
        conv1d_weight_2,
        conv1d_weight_3,
        conv1d_bias,
        x_proj_weight_dt,
        x_proj_weight_B_0,
        x_proj_weight_B_1,
        x_proj_weight_B_2,
        x_proj_weight_B_3,
        x_proj_weight_B_4,
        x_proj_weight_B_5,
        x_proj_weight_B_6,
        x_proj_weight_B_7,
        x_proj_weight_B_8,
        x_proj_weight_B_9,
        x_proj_weight_B_10,
        x_proj_weight_B_11,
        x_proj_weight_B_12,
        x_proj_weight_B_13,
        x_proj_weight_B_14,
        x_proj_weight_B_15,
        x_proj_weight_C_0,
        x_proj_weight_C_1,
        x_proj_weight_C_2,
        x_proj_weight_C_3,
        x_proj_weight_C_4,
        x_proj_weight_C_5,
        x_proj_weight_C_6,
        x_proj_weight_C_7,
        x_proj_weight_C_8,
        x_proj_weight_C_9,
        x_proj_weight_C_10,
        x_proj_weight_C_11,
        x_proj_weight_C_12,
        x_proj_weight_C_13,
        x_proj_weight_C_14,
        x_proj_weight_C_15,
        dt_proj_weight,
        dt_proj_bias,
        A_log_0,
        A_log_1,
        A_log_2,
        A_log_3,
        A_log_4,
        A_log_5,
        A_log_6,
        A_log_7,
        A_log_8,
        A_log_9,
        A_log_10,
        A_log_11,
        A_log_12,
        A_log_13,
        A_log_14,
        A_log_15,
        D_param,
        out_proj_weight,
        out_proj_bias,
    ):
        hidden = in_hidden_state
        conv_old = [in_conv_state_0, in_conv_state_1, in_conv_state_2, in_conv_state_3]
        ssm_old = [
            in_ssm_state_0, in_ssm_state_1, in_ssm_state_2, in_ssm_state_3,
            in_ssm_state_4, in_ssm_state_5, in_ssm_state_6, in_ssm_state_7,
            in_ssm_state_8, in_ssm_state_9, in_ssm_state_10, in_ssm_state_11,
            in_ssm_state_12, in_ssm_state_13, in_ssm_state_14, in_ssm_state_15,
        ]
        B_w = [
            x_proj_weight_B_0, x_proj_weight_B_1, x_proj_weight_B_2, x_proj_weight_B_3,
            x_proj_weight_B_4, x_proj_weight_B_5, x_proj_weight_B_6, x_proj_weight_B_7,
            x_proj_weight_B_8, x_proj_weight_B_9, x_proj_weight_B_10, x_proj_weight_B_11,
            x_proj_weight_B_12, x_proj_weight_B_13, x_proj_weight_B_14, x_proj_weight_B_15,
        ]
        C_w = [
            x_proj_weight_C_0, x_proj_weight_C_1, x_proj_weight_C_2, x_proj_weight_C_3,
            x_proj_weight_C_4, x_proj_weight_C_5, x_proj_weight_C_6, x_proj_weight_C_7,
            x_proj_weight_C_8, x_proj_weight_C_9, x_proj_weight_C_10, x_proj_weight_C_11,
            x_proj_weight_C_12, x_proj_weight_C_13, x_proj_weight_C_14, x_proj_weight_C_15,
        ]
        A_log = [
            A_log_0, A_log_1, A_log_2, A_log_3, A_log_4, A_log_5, A_log_6, A_log_7,
            A_log_8, A_log_9, A_log_10, A_log_11, A_log_12, A_log_13, A_log_14, A_log_15,
        ]
        conv_w = [conv1d_weight_0, conv1d_weight_1, conv1d_weight_2, conv1d_weight_3]

        # 1. Input projection
        x_pre = wrap32(qmul(hidden, in_proj_weight_x) + to_signed(in_proj_bias_x, 32))
        z_pre = wrap32(qmul(hidden, in_proj_weight_z) + to_signed(in_proj_bias_z, 32))

        # 2. Conv state shift (insert x_pre at end)
        conv_new = [conv_old[1], conv_old[2], conv_old[3], x_pre]

        # 3. Conv sum
        conv_sum = qmul(conv_new[0], conv_w[0])
        conv_sum = wrap32(conv_sum + qmul(conv_new[1], conv_w[1]))
        conv_sum = wrap32(conv_sum + qmul(conv_new[2], conv_w[2]))
        conv_sum = wrap32(conv_sum + qmul(conv_new[3], conv_w[3]))
        conv_sum_b = wrap32(conv_sum + to_signed(conv1d_bias, 32))

        # 4. SiLU
        x = silu_approx(conv_sum_b)

        # 5. dt, B, C
        dt_raw = qmul(x, x_proj_weight_dt)
        B = [qmul(x, B_w[n]) for n in range(16)]
        C = [qmul(x, C_w[n]) for n in range(16)]

        # 6. dt projection
        dt_pre = wrap32(qmul(dt_raw, dt_proj_weight) + to_signed(dt_proj_bias, 32))
        dt = softplus_approx(dt_pre)

        # 7. A
        A = [wrap32(-exp_approx(A_log[n])) for n in range(16)]

        # 8. dA, dB
        dA = [exp_approx(qmul(dt, A[n])) for n in range(16)]
        dB = [qmul(dt, B[n]) for n in range(16)]

        # 9. SSM state update
        ssm_new = [wrap32(qmul(ssm_old[n], dA[n]) + qmul(x, dB[n])) for n in range(16)]

        # 10. SSM readout (left-fold)
        y_ssm = qmul(ssm_new[0], C[0])
        for n in range(1, 16):
            y_ssm = wrap32(y_ssm + qmul(ssm_new[n], C[n]))

        # 11. Skip
        y_skip = qmul(D_param, x)
        y_pre_gate = wrap32(y_ssm + y_skip)

        # 12. SiLU gate
        z = silu_approx(z_pre)
        y = qmul(y_pre_gate, z)

        # 13. Output projection
        out_hidden = wrap32(qmul(y, out_proj_weight) + to_signed(out_proj_bias, 32))

        result = {
            "out_hidden_state": to_unsigned(out_hidden, 32),
            "out_conv_state_0": to_unsigned(conv_new[0], 32),
            "out_conv_state_1": to_unsigned(conv_new[1], 32),
            "out_conv_state_2": to_unsigned(conv_new[2], 32),
            "out_conv_state_3": to_unsigned(conv_new[3], 32),
        }
        for n in range(16):
            result[f"out_ssm_state_{n}"] = to_unsigned(ssm_new[n], 32)
        return result
