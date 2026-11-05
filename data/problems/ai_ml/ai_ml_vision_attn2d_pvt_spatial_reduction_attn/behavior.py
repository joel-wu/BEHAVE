def lane(word, index):
    value = (word >> (16 * index)) & 65535
    return value - 65536 if value >= 32768 else value


def clip(value):
    return max(-32768, min(32767, value))


def root(value):
    result = 0
    for bit in range(32):
        trial = result + (1 << (31-bit))
        if trial * trial <= value:
            result = trial
    return result


EXP = (65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954, 30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566, 14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353, 6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473, 3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641, 1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775, 728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366, 344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173, 162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22)


class AiMlVisionAttn2dPvtSpatialReductionAttn:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_sr_weight, in_sr_bias, in_norm_weight, in_norm_bias, in_q_weight, in_q_bias, in_kv_weight, in_kv_bias, in_proj_weight, in_proj_bias):
        # Learned stride-two convolution: four spatial tokens, four channels.
        reduced = [0]*16
        normalized = [0]*16
        for p in range(4):
            for co in range(4):
                total = 0
                for ci in range(4):
                    for dy in range(2):
                        for dx in range(2):
                            t = (2*(p//2)+dy)*4 + 2*(p%2)+dx
                            wi = ((co*4+ci)*2+dy)*2+dx
                            total += lane(in_x, t*4+ci)*lane(in_sr_weight, wi)
                reduced[p*4+co] = clip(total//256+lane(in_sr_bias, co))
            mean = (reduced[p*4]+reduced[p*4+1]+reduced[p*4+2]+reduced[p*4+3])//4
            variance = 0
            for co in range(4):
                delta = reduced[p*4+co]-mean
                variance += delta*delta
            scale = root(variance//4+1)
            for co in range(4):
                unit = ((reduced[p*4+co]-mean)*256)//scale
                normalized[p*4+co] = clip(unit*lane(in_norm_weight,co)//256+lane(in_norm_bias,co))
        query = [0]*64
        kv = [0]*32
        for t in range(16):
            for co in range(4):
                total = 0
                for ci in range(4):
                    total += lane(in_x,t*4+ci)*lane(in_q_weight,co*4+ci)
                query[t*4+co] = clip(total//256+lane(in_q_bias,co))
        for p in range(4):
            for co in range(8):
                total = 0
                for ci in range(4):
                    total += normalized[p*4+ci]*lane(in_kv_weight,co*4+ci)
                kv[p*8+co] = clip(total//256+lane(in_kv_bias,co))
        context = [0]*64
        for t in range(16):
            scores = [0]*4
            weights = [0]*4
            for p in range(4):
                total = 0
                for ci in range(4):
                    total += query[t*4+ci]*kv[p*8+ci]
                scores[p] = total//512
            peak = max(max(scores[0],scores[1]),max(scores[2],scores[3]))
            denom = 0
            for p in range(4):
                index = (peak-scores[p]+8)//16
                weights[p] = EXP[index] if index <= 128 else 0
                denom += weights[p]
            for co in range(4):
                total = 0
                for p in range(4):
                    total += weights[p]*kv[p*8+4+co]
                context[t*4+co] = total//denom
        result = 0
        for t in range(16):
            for co in range(4):
                total = 0
                for ci in range(4):
                    total += context[t*4+ci]*lane(in_proj_weight,co*4+ci)
                y = clip(total//256+lane(in_proj_bias,co))
                result |= (y & 65535) << (16*(t*4+co))
        return {'out_data':result}
