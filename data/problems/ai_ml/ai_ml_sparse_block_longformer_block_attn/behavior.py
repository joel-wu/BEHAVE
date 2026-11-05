EXP = [65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954, 30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566, 14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353, 6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473, 3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641, 1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775, 728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366, 344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173, 162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82, 77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39, 36, 34, 32, 30, 28, 27, 25, 23, 22]


def signed16(word):
    value = word & 65535
    return value - 65536 if value >= 32768 else value


class LongformerBlockAttention:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_query, in_key, in_value, in_global_query,
                in_global_key, in_global_value, in_masked, in_global):
        result = 0
        for i in range(8):
            global_query = (in_global >> i) & 1
            q = in_global_query if global_query else in_query
            k = in_global_key if global_query else in_key
            v = in_global_value if global_query else in_value
            allowed = [0] * 8
            scores = [0] * 8
            weights = [0] * 8
            peak = 0
            found = 0
            for j in range(8):
                if ((in_masked >> i) & 1) == 0 and ((in_masked >> j) & 1) == 0:
                    if global_query or ((in_global >> j) & 1) or (i-2 <= j and j <= i+2):
                        allowed[j] = 1
                        total = 0
                        for c in range(4):
                            total += signed16(q >> (16*(4*i+c))) * signed16(k >> (16*(4*j+c)))
                        scores[j] = total // 512
                        if found == 0 or scores[j] > peak:
                            peak = scores[j]
                        found = 1
            denominator = 0
            for j in range(8):
                if allowed[j]:
                    grid = (peak-scores[j]+8) // 16
                    if grid <= 128:
                        weights[j] = EXP[grid]
                        denominator += weights[j]
            for c in range(4):
                numerator = 0
                for j in range(8):
                    numerator += weights[j] * signed16(v >> (16*(4*j+c)))
                value = numerator // denominator if denominator else 0
                result |= (value & 65535) << (16*(4*i+c))
        return {"out_attn_out": result}
