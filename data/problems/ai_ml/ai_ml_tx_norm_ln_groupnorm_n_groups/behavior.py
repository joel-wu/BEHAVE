def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div_toward_zero(n, d):
    an = n if n >= 0 else -n
    q = an // d
    return q if n >= 0 else -q


def isqrt_floor(n):
    x = 0
    bit = 1 << 30
    while bit > n:
        bit >>= 2
    while bit != 0:
        if n >= x + bit:
            n -= x + bit
            x = (x >> 1) + bit
        else:
            x >>= 1
        bit >>= 2
    return x


class ai_ml_tx_norm_ln_groupnorm_n_groups:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_y = 0

    def process(self, in_x, in_gamma, in_beta):
        xs = [to_signed((in_x >> (16 * c)) & 0xFFFF, 16) for c in range(8)]
        gammas = [to_signed((in_gamma >> (16 * c)) & 0xFFFF, 16) for c in range(8)]
        betas = [to_signed((in_beta >> (16 * c)) & 0xFFFF, 16) for c in range(8)]

        ys = [0] * 8

        for g in range(2):
            base = g * 4
            x0 = xs[base + 0]
            x1 = xs[base + 1]
            x2 = xs[base + 2]
            x3 = xs[base + 3]

            sum_g = x0 + x1 + x2 + x3
            mean_g = trunc_div_toward_zero(sum_g, 4)

            centered0 = x0 - mean_g
            centered1 = x1 - mean_g
            centered2 = x2 - mean_g
            centered3 = x3 - mean_g

            sqsum_g = (
                centered0 * centered0
                + centered1 * centered1
                + centered2 * centered2
                + centered3 * centered3
            )
            var_g = trunc_div_toward_zero(sqsum_g, 4)
            den_g = isqrt_floor(var_g + 1)

            centered = [centered0, centered1, centered2, centered3]
            for i in range(4):
                c = base + i
                norm_num = centered[i] * 256
                norm_i = trunc_div_toward_zero(norm_num, den_g)
                prod_c = gammas[c] * norm_i
                scaled_c = trunc_div_toward_zero(prod_c, 256)
                affine_c = scaled_c + betas[c]
                y_pre_sat_c = trunc_div_toward_zero(affine_c, 256)
                ys[c] = to_unsigned(y_pre_sat_c, 16)

        out_y = 0
        for c in range(8):
            out_y |= ys[c] << (16 * c)
        self.out_y = to_unsigned(out_y, 128)
        return {"out_y": self.out_y}