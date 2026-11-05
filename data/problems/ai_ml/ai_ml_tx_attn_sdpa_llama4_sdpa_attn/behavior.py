# A NoROPE inference instance: two Q heads share one K/V head.
EXP = (65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954, 30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566, 14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353, 6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473, 3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641, 1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775, 728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366, 344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173, 162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22)


def lane(word, index):
    value = (word >> (16*index)) & 65535
    return value-65536 if value >= 32768 else value


def sat(x):
    return min(32767,max(-32768,x))


def project(x, weight, bias, width):
    output = [0]*width
    for i in range(width):
        acc = lane(bias,i)*256
        for j in range(8):
            acc += x[j]*lane(weight,i*8+j)
        output[i] = sat(acc//256)
    return output


class Llama4SdpaAttn:
    def __init__(self):
        self.reset()

    def reset(self):
        self.seq_len = 0
        self.key_history = [0]*32
        self.value_history = [0]*32

    def process(self, in_hidden_states, in_q_weight, in_q_bias, in_k_weight,
                in_k_bias, in_v_weight, in_v_bias, in_proj_weight, in_proj_bias,
                in_attention_mask_valid, in_attention_mask, in_attention_bias):
        x = [lane(in_hidden_states,i) for i in range(8)]
        q = project(x,in_q_weight,in_q_bias,8)
        key = project(x,in_k_weight,in_k_bias,4)
        value = project(x,in_v_weight,in_v_bias,4)
        if self.seq_len == 8:
            for i in range(28):
                self.key_history[i] = self.key_history[i+4]
                self.value_history[i] = self.value_history[i+4]
        else:
            self.seq_len = min(8, self.seq_len + 1)
        for d in range(4):
            self.key_history[(self.seq_len-1)*4+d] = key[d]
            self.value_history[(self.seq_len-1)*4+d] = value[d]
        context = [0]*8
        for h in range(2):
            scores = [0]*8
            maximum = -1000000000
            for j in range(8):
                if j < self.seq_len:
                    acc = 0
                    for d in range(4):
                        acc += q[h*4+d]*self.key_history[j*4+d]
                    scores[j] = acc//512
                    if in_attention_mask_valid:
                        scores[j] += lane(in_attention_bias,j)
                    if not in_attention_mask_valid or ((in_attention_mask >> j)&1) == 0:
                        maximum = max(maximum,scores[j])
            weights = [0]*8
            total = 0
            for j in range(8):
                if j < self.seq_len and (not in_attention_mask_valid or ((in_attention_mask >> j)&1) == 0):
                    index = (maximum-scores[j]+8)//16
                    if index <= 128:
                        weights[j] = EXP[index]
                        total += weights[j]
            if total:
                for d in range(4):
                    acc = 0
                    for j in range(8):
                        if j < self.seq_len:
                            acc += weights[j]*self.value_history[j*4+d]
                    context[h*4+d] = acc//total
        output = project(context,in_proj_weight,in_proj_bias,8)
        packed = 0
        for i in range(8):
            packed |= (output[i]&65535) << (16*i)
        return {"out_attn_output":packed}
