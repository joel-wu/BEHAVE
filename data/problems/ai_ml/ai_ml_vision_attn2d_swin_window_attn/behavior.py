# Two-head, 2x2-window attention. Integer lanes represent signed Q8.8.
EXP = (65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954, 30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566, 14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353, 6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473, 3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641, 1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775, 728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366, 344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173, 162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22)


def lane(word, index):
    value = (word >> (16 * index)) & 65535
    return value - 65536 if value >= 32768 else value


def sat(value):
    return min(32767, max(-32768, value))


def project(x, weight, bias):
    result = [0] * 32
    for row in range(4):
        for col in range(8):
            acc = lane(bias, col) * 256
            for k in range(8):
                acc += x[row * 8 + k] * lane(weight, col * 8 + k)
            result[row * 8 + col] = sat(acc // 256)
    return result


class SwinWindowAttention2D:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_q_weight, in_q_bias, in_k_weight, in_k_bias,
                in_v_weight, in_v_bias, in_proj_weight, in_proj_bias,
                in_rel_pos_bias_table, in_mask):
        x = [lane(in_x, i) for i in range(32)]
        q = project(x, in_q_weight, in_q_bias)
        k = project(x, in_k_weight, in_k_bias)
        v = project(x, in_v_weight, in_v_bias)
        context = [0] * 32
        for row in range(4):
            for head in range(2):
                scores = [0] * 4
                maximum = -1000000000
                for col in range(4):
                    dot = 0
                    for dim in range(4):
                        dot += q[row*8+head*4+dim] * k[col*8+head*4+dim]
                    offset = (row//2-col//2+1)*3 + row%2-col%2+1
                    scores[col] = dot//512 + lane(in_rel_pos_bias_table, offset*2+head)
                    if ((in_mask >> (row*4+col)) & 1) == 0:
                        maximum = max(maximum, scores[col])
                weights = [0] * 4
                total = 0
                for col in range(4):
                    if ((in_mask >> (row*4+col)) & 1) == 0:
                        index = (maximum-scores[col]+8)//16
                        if index <= 128:
                            weights[col] = EXP[index]
                            total += weights[col]
                if total != 0:
                    for dim in range(4):
                        acc = 0
                        for col in range(4):
                            acc += weights[col] * v[col*8+head*4+dim]
                        context[row*8+head*4+dim] = acc//total
        y = project(context, in_proj_weight, in_proj_bias)
        packed = 0
        for i in range(32):
            packed |= (y[i] & 65535) << (16*i)
        return {"out_y": packed}
