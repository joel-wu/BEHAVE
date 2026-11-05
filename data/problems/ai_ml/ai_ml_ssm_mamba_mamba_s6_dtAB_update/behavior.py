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


def add(a, b):
    return sat(a + b)


def mul(a, b):
    return sat(rne(a * b, S))


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

class ai_ml_ssm_mamba_mamba_s6_dtAB_update:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_state, in_x, in_dt, in_A, in_B, in_C, in_D, in_z,
                in_dt_bias, in_has_D, in_has_z, in_has_dt_bias, in_dt_softplus):
        state, x, dt = s32(in_state), s32(in_x), s32(in_dt)
        if in_has_dt_bias:
            dt = add(dt, s32(in_dt_bias))
        if in_dt_softplus:
            dt = softplus16(dt)
        decay = exp16(mul(s32(in_A), dt))
        drive = mul(mul(s32(in_B), dt), x)
        next_state = add(mul(state, decay), drive)
        output = mul(next_state, s32(in_C))
        if in_has_D:
            output = add(output, mul(x, s32(in_D)))
        if in_has_z:
            output = mul(output, silu16(s32(in_z)))
        return {"out_state": next_state & 0xFFFFFFFF, "out_out": output & 0xFFFFFFFF}
