def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero(n, d):
    sign = -1 if (n < 0) ^ (d < 0) else 1
    an = -n if n < 0 else n
    ad = -d if d < 0 else d
    q = an // ad
    return -q if sign < 0 else q


def qmul(a, b):
    return to_signed(trunc_div_toward_zero(to_signed(a, 64) * to_signed(b, 64), 1 << 32), 64)


def qdiv(a, b):
    return to_signed(trunc_div_toward_zero(to_signed(a, 64) << 32, to_signed(b, 64)), 64)


class CyclicReductionStage:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a_cw_m1,
        in_a_cc_m1,
        in_a_ce_m1,
        in_a_cw_0,
        in_a_cc_0,
        in_a_ce_0,
        in_a_cw_p1,
        in_a_cc_p1,
        in_a_ce_p1,
        in_periodic_collapse,
    ):
        one_q32_32 = 0x0000000100000000
        accm1 = in_a_cc_m1 if to_signed(in_a_cc_m1, 64) != 0 else one_q32_32
        accp1 = in_a_cc_p1 if to_signed(in_a_cc_p1, 64) != 0 else one_q32_32

        term_w = qdiv(qmul(in_a_cw_0, in_a_cw_m1), accm1)
        term_c_left = qdiv(qmul(in_a_cw_0, in_a_ce_m1), accm1)
        term_c_right = qdiv(qmul(in_a_ce_0, in_a_cw_p1), accp1)
        term_e = qdiv(qmul(in_a_ce_0, in_a_ce_p1), accp1)

        ac_cw_base = to_signed(-term_w, 64)
        ac_cc_base = to_signed(to_signed(in_a_cc_0, 64) - term_c_left - term_c_right, 64)
        ac_ce_base = to_signed(-term_e, 64)

        if in_periodic_collapse:
            out_ac_cw = 0
            out_ac_cc = to_signed(ac_cc_base + ac_cw_base + ac_ce_base, 64)
            out_ac_ce = 0
        else:
            out_ac_cw = ac_cw_base
            out_ac_cc = ac_cc_base
            out_ac_ce = ac_ce_base

        return {
            "out_ac_cw": to_unsigned(out_ac_cw, 64),
            "out_ac_cc": to_unsigned(out_ac_cc, 64),
            "out_ac_ce": to_unsigned(out_ac_ce, 64),
        }