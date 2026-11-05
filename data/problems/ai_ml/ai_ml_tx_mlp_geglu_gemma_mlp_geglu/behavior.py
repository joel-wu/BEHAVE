from fp_lib import fp32_add, fp32_mul

GELU_TAIL_Q30 = (0, 16358963, 31882236, 46572264, 60433112, 73470446, 85691505, 97105076, 107721450, 117552381, 126611033, 134911925, 142470870, 149304907, 155432230, 160872113, 165644830, 169771573, 173274370, 176175991, 178499867, 180269994, 181510845, 182247279, 182504450, 182307718, 181682560, 180654483, 179248939, 177491248, 175406510, 173019538, 170354782, 167436259, 164287495, 160931457, 157390507, 153686342, 149839956, 145871596, 141800726, 137645995, 133425213, 129155328, 124852410, 120531640, 116207299, 111892768, 107600529, 103342169, 99128387, 94969009, 90873003, 86848495, 82902792, 79042409, 75273086, 71599827, 68026921, 64557978, 61195959, 57943210, 54801496, 51772036, 48855536, 46052227, 43361896, 40783922, 38317310, 35960726, 33712524, 31570784, 29533337, 27597799, 25761596, 24021991, 22376112, 20820975, 19353504, 17970557, 16668943, 15445443, 14296822, 13219852, 12211319, 11268042, 10386880, 9564746, 8798615, 8085530, 7422613, 6807066, 6236180, 5707338, 5218016, 4765788, 4348326, 3963403, 3608889, 3282756, 2983074, 2708009, 2455826, 2224880, 2013622, 1820588, 1644404, 1483777, 1337496, 1204426, 1083509, 973755, 874243, 784115, 702575, 628886, 562364, 502377, 448341, 399719, 356015, 316774, 281577, 250043, 221819, 196586, 174050, 153945, 136027, 120075, 105889, 93287, 82103, 72188, 63408, 55640, 48776, 42716, 37373, 32665, 28522, 24880, 21682, 18876, 16417, 14264, 12382, 10737, 9301, 8050, 6960, 6012, 5187, 4472, 3851, 3313, 2848, 2445, 2098, 1798, 1539, 1316, 1125, 960, 819, 697, 594, 505, 429, 364, 308, 261, 221, 187, 158, 133, 112, 94, 79, 67, 56, 47, 39, 33, 28, 23, 19, 16, 13, 11, 9, 8, 6, 5, 4, 4, 3, 2, 2, 2, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def q30_word(value):
    if value == 0:
        return 0
    top = 0
    for i in range(31):
        if value >> i:
            top = i
    if top <= 23:
        significand = value << (23 - top)
    else:
        shift = top - 23
        significand = value >> shift
        remainder = value & ((1 << shift) - 1)
        halfway = 1 << (shift - 1)
        if remainder > halfway or (remainder == halfway and (significand & 1)):
            significand = significand + 1
        if significand == 0x1000000:
            significand = significand >> 1
            top = top + 1
    return ((top + 97) << 23) | (significand & 0x7FFFFF)


def gelu_word(x):
    x = x & 0xFFFFFFFF
    a = x & 0x7FFFFFFF
    exponent = (a >> 23) & 255
    if a > 0x7F800000:
        return 0x7FC00000
    if exponent == 0:
        return x & 0x80000000
    if a >= 0x41000000:
        return 0x80000000 if x >> 31 else x
    q = 0
    if exponent >= 111:
        q = ((a & 0x7FFFFF) | 0x800000) >> (134 - exponent)
    j = q >> 11
    r = q & 2047
    correction = (GELU_TAIL_Q30[j] * (2048-r) + GELU_TAIL_Q30[j+1] * r) // 2048
    h = q30_word(correction)
    return (h | 0x80000000) if x >> 31 else fp32_add(x, h | 0x80000000)


class GemmaMLPGeGLU:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x_0,
        in_x_1,
        in_gate_proj_weight_0_0,
        in_gate_proj_weight_0_1,
        in_gate_proj_weight_1_0,
        in_gate_proj_weight_1_1,
        in_up_proj_weight_0_0,
        in_up_proj_weight_0_1,
        in_up_proj_weight_1_0,
        in_up_proj_weight_1_1,
        in_down_proj_weight_0_0,
        in_down_proj_weight_0_1,
        in_down_proj_weight_1_0,
        in_down_proj_weight_1_1,
    ):
        g0 = fp32_add(fp32_mul(in_gate_proj_weight_0_0, in_x_0), fp32_mul(in_gate_proj_weight_0_1, in_x_1))
        g1 = fp32_add(fp32_mul(in_gate_proj_weight_1_0, in_x_0), fp32_mul(in_gate_proj_weight_1_1, in_x_1))
        u0 = fp32_add(fp32_mul(in_up_proj_weight_0_0, in_x_0), fp32_mul(in_up_proj_weight_0_1, in_x_1))
        u1 = fp32_add(fp32_mul(in_up_proj_weight_1_0, in_x_0), fp32_mul(in_up_proj_weight_1_1, in_x_1))
        gg0 = gelu_word(g0)
        gg1 = gelu_word(g1)
        m0 = fp32_mul(gg0, u0)
        m1 = fp32_mul(gg1, u1)
        y0 = fp32_add(fp32_mul(in_down_proj_weight_0_0, m0), fp32_mul(in_down_proj_weight_0_1, m1))
        y1 = fp32_add(fp32_mul(in_down_proj_weight_1_0, m0), fp32_mul(in_down_proj_weight_1_1, m1))
        return {"out_y_0": (y0 & 0xFFFFFFFF), "out_y_1": (y1 & 0xFFFFFFFF)}
