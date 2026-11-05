from fp_lib import fp32_add, fp32_sub, fp32_mul, fp32_div, fp32_sqrt


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


FOUR = 0x40800000
EPSILON = 0x3727C5AC


class ai_ml_tx_norm_ln_bert_layernorm:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x0, in_x1, in_x2, in_x3,
        in_gamma0, in_gamma1, in_gamma2, in_gamma3,
        in_beta0, in_beta1, in_beta2, in_beta3,
    ):
        sum01 = fp32_add(in_x0, in_x1)
        sum23 = fp32_add(in_x2, in_x3)
        sum_w = fp32_add(sum01, sum23)
        mean = fp32_div(sum_w, FOUR)

        d0 = fp32_sub(in_x0, mean)
        d1 = fp32_sub(in_x1, mean)
        d2 = fp32_sub(in_x2, mean)
        d3 = fp32_sub(in_x3, mean)

        s0 = fp32_mul(d0, d0)
        s1 = fp32_mul(d1, d1)
        s2 = fp32_mul(d2, d2)
        s3 = fp32_mul(d3, d3)

        vsum01 = fp32_add(s0, s1)
        vsum23 = fp32_add(s2, s3)
        vsum = fp32_add(vsum01, vsum23)
        variance = fp32_div(vsum, FOUR)

        variance_eps = fp32_add(variance, EPSILON)
        stddev = fp32_sqrt(variance_eps)

        n0 = fp32_div(d0, stddev)
        n1 = fp32_div(d1, stddev)
        n2 = fp32_div(d2, stddev)
        n3 = fp32_div(d3, stddev)

        m0 = fp32_mul(n0, in_gamma0)
        m1 = fp32_mul(n1, in_gamma1)
        m2 = fp32_mul(n2, in_gamma2)
        m3 = fp32_mul(n3, in_gamma3)

        y0 = fp32_add(m0, in_beta0)
        y1 = fp32_add(m1, in_beta1)
        y2 = fp32_add(m2, in_beta2)
        y3 = fp32_add(m3, in_beta3)

        return {
            "out_y0": to_unsigned(y0, 32),
            "out_y1": to_unsigned(y1, 32),
            "out_y2": to_unsigned(y2, 32),
            "out_y3": to_unsigned(y3, 32),
        }