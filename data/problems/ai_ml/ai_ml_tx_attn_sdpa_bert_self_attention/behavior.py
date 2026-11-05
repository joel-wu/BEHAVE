NEG_EXP2_NODES = (1073741824, 1050733751, 1028218693, 1006186087, 984625594, 963527098, 942880699, 922676710, 902905651, 883558244, 864625413, 846098274, 827968132, 810226483, 792865000, 775875538, 759250125, 742980960, 727060411, 711481005, 696235434, 681316545, 666717336, 652430958, 638450708, 624770026, 611382493, 598281827, 585461881, 572916640, 560640218, 548626854, 536870912)


def signed(value, width):
    value = value & ((1 << width) - 1)
    return value - (1 << width) if value & (1 << (width - 1)) else value


def trunc_div(value, divisor):
    return -((-value) // divisor) if value < 0 else value // divisor


def exp_weight(distance):
    # Nonnegative natural-log distance in Q16; Q30 output.
    if distance >= 1048576:
        return 0
    z = distance * 94548 // 65536
    n = z >> 16
    fraction = z & 65535
    j = fraction >> 11
    r = fraction & 2047
    a = NEG_EXP2_NODES[j]
    b = NEG_EXP2_NODES[j + 1]
    return (a - ((a - b) * r // 2048)) >> n


def sqrt_scale(dimension):
    radicand = dimension << 32
    root = 0
    remainder = 0
    for i in range(31, -1, -1):
        remainder = (remainder << 2) | ((radicand >> (2 * i)) & 3)
        trial = (root << 2) | 1
        if remainder >= trial:
            remainder = remainder - trial
            root = (root << 1) | 1
        else:
            root = root << 1
    return root


class AiMlTxAttnSdpaBertSelfAttention:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_hidden_states, in_attention_mask):
        x = [[0] * 4 for _ in range(4)]
        mask = [[0] * 4 for _ in range(4)]
        y = [[0] * 4 for _ in range(4)]

        for s in range(4):
            for h in range(4):
                idx = 4 * s + h
                x[s][h] = signed((in_hidden_states >> (16 * idx)) & 0xFFFF, 16)

        for q in range(4):
            for k in range(4):
                idx = 4 * q + k
                mask[q][k] = signed((in_attention_mask >> (4 * idx)) & 0xF, 4)

        for n in range(2):
            base = 2 * n
            for q in range(4):
                score = [0] * 4
                for k in range(4):
                    dot = x[q][base + 0] * x[k][base + 0] + x[q][base + 1] * x[k][base + 1]
                    scaled = trunc_div(dot * 46341, 65536)
                    score[k] = scaled + (mask[q][k] << 16)

                m = score[0]
                if score[1] > m:
                    m = score[1]
                if score[2] > m:
                    m = score[2]
                if score[3] > m:
                    m = score[3]

                w = [0] * 4
                for k in range(4):
                    w[k] = exp_weight(m - score[k])

                wsum = w[0] + w[1] + w[2] + w[3]

                p = [0] * 4
                for k in range(4):
                    if wsum == 0:
                        p[k] = 0
                    else:
                        p[k] = (w[k] << 16) // wsum

                for d in range(2):
                    acc = (
                        p[0] * x[0][base + d]
                        + p[1] * x[1][base + d]
                        + p[2] * x[2][base + d]
                        + p[3] * x[3][base + d]
                    )
                    y[q][base + d] = trunc_div(acc, 65536)

        out_attn_output = 0
        for s in range(4):
            for h in range(4):
                idx = 4 * s + h
                out_attn_output |= (y[s][h] & 65535) << (16 * idx)

        return {"out_attn_output": out_attn_output}
