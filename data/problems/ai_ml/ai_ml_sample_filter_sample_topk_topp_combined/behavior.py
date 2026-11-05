from fp_lib import fp32_div, fp32_cmp

NEG_INF = 0xff800000
POS_INF = 0x7f800000
EXP = (65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954, 30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566, 14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353, 6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473, 3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641, 1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775, 728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366, 344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173, 162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22)


def grid(value):
    # Round 16*x to nearest integer, ties away from zero. Called only on finite x.
    exponent = (value >> 23) & 255
    if exponent == 0:
        return 0
    mantissa = (value & 0x7fffff) | 0x800000
    shift = exponent - 146
    if shift >= 0:
        code = mantissa << shift
    elif shift < -24:
        code = 0
    else:
        code = (mantissa + (1 << (-shift-1))) >> -shift
    return -code if value >> 31 else code


class AiMlSampleFilterSampleTopkToppCombined:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_score0, in_score1, in_score2, in_score3,
                in_score4, in_score5, in_score6, in_score7,
                in_temperature, in_top_k, in_top_p,
                in_min_tokens_to_keep_k, in_min_tokens_to_keep_p):
        scores = [in_score0,in_score1,in_score2,in_score3,
                  in_score4,in_score5,in_score6,in_score7]
        te = (in_temperature >> 23) & 255
        pe = (in_top_p >> 23) & 255
        invalid = (in_temperature >> 31) != 0 or te == 0 or te == 255
        invalid = invalid or pe == 255 or ((in_top_p >> 31) != 0 and (in_top_p & 0x7fffffff) != 0)
        invalid = invalid or ((in_top_p >> 31) == 0 and in_top_p > 0x3f800000)
        final = [NEG_INF]*8
        token = 0
        if not invalid:
            scaled = [fp32_div(s,in_temperature) for s in scores]
            for i in range(8):
                if ((scaled[i] >> 23) & 255) == 255 and (scaled[i] & 0x7fffff) != 0:
                    scaled[i] = NEG_INF
            order = [0,1,2,3,4,5,6,7]
            # Stable ascending score order. Top-k uses its threshold, not an
            # exactly-k truncation that would discard tied boundary scores.
            for end in range(7,0,-1):
                for i in range(end):
                    if fp32_cmp(scaled[order[i]],scaled[order[i+1]]) > 0:
                        order[i],order[i+1] = order[i+1],order[i]
            count = min(8,max(1,in_top_k,in_min_tokens_to_keep_k))
            threshold = scaled[order[8-count]]
            for i in range(8):
                if fp32_cmp(scaled[i],threshold) >= 0:
                    final[i] = scaled[i]
            weights = [0]*8
            peak = scaled[order[7]]
            for i in range(8):
                if peak == POS_INF:
                    weights[i] = 65536 if final[i] == POS_INF else 0
                elif final[i] != NEG_INF:
                    distance = grid(peak)-grid(final[i])
                    if distance <= 128:
                        weights[i] = EXP[distance]
            denominator = 1 << (149 if pe == 0 else 150-pe)
            numerator = in_top_p & 0x7fffff
            if pe != 0:
                numerator |= 0x800000
            total = sum(weights)
            cumulative = 0
            minimum = min(8,max(1,in_min_tokens_to_keep_p))
            # Masked top-k entries have zero probability; retaining them does
            # not revive their logits. Ascending ties use lower index first.
            for rank in range(8):
                i = order[rank]
                cumulative += weights[i]
                if rank < 8-minimum and cumulative*denominator <= total*(denominator-numerator):
                    final[i] = NEG_INF
            for i in range(1,8):
                if fp32_cmp(final[i],final[token]) > 0:
                    token = i
        return {"out_score0":final[0],"out_score1":final[1],
                "out_score2":final[2],"out_score3":final[3],
                "out_score4":final[4],"out_score5":final[5],
                "out_score6":final[6],"out_score7":final[7],
                "out_token":token,"out_error":int(invalid)}
