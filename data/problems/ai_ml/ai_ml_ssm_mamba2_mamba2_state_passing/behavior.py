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



class Mamba2StatePassing:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_first_chunk, in_last_chunk, in_has_initstate, in_has_seq_idx,
                in_prev_state, in_state_update, in_dA_chunk_cumsum, in_initial_state,
                in_prev_seq_idx, in_seq_idx_new):
        if in_first_chunk:
            carried = s32(in_initial_state) if in_has_initstate else 0
            previous_index = 0
        else:
            carried = s32(in_prev_state)
            previous_index = in_prev_seq_idx
        if in_has_seq_idx and in_seq_idx_new != previous_index:
            contribution = 0
        else:
            contribution = mul(exp16(s32(in_dA_chunk_cumsum)), carried)
        next_state = add(contribution, s32(in_state_update))
        return {
            "out_last_chunk": in_last_chunk,
            "out_out": carried & 0xFFFFFFFF,
            "out_next_state": next_state & 0xFFFFFFFF,
            "out_final_state": (next_state if in_last_chunk else 0) & 0xFFFFFFFF,
            "out_next_prev_seq_idx": in_seq_idx_new if in_has_seq_idx else 0,
        }
