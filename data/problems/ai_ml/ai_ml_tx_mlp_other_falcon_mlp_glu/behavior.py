GELU_TAIL_Q30 = (0, 16358963, 31882236, 46572264, 60433112, 73470446, 85691505, 97105076, 107721450, 117552381, 126611033, 134911925, 142470870, 149304907, 155432230, 160872113, 165644830, 169771573, 173274370, 176175991, 178499867, 180269994, 181510845, 182247279, 182504450, 182307718, 181682560, 180654483, 179248939, 177491248, 175406510, 173019538, 170354782, 167436259, 164287495, 160931457, 157390507, 153686342, 149839956, 145871596, 141800726, 137645995, 133425213, 129155328, 124852410, 120531640, 116207299, 111892768, 107600529, 103342169, 99128387, 94969009, 90873003, 86848495, 82902792, 79042409, 75273086, 71599827, 68026921, 64557978, 61195959, 57943210, 54801496, 51772036, 48855536, 46052227, 43361896, 40783922, 38317310, 35960726, 33712524, 31570784, 29533337, 27597799, 25761596, 24021991, 22376112, 20820975, 19353504, 17970557, 16668943, 15445443, 14296822, 13219852, 12211319, 11268042, 10386880, 9564746, 8798615, 8085530, 7422613, 6807066, 6236180, 5707338, 5218016, 4765788, 4348326, 3963403, 3608889, 3282756, 2983074, 2708009, 2455826, 2224880, 2013622, 1820588, 1644404, 1483777, 1337496, 1204426, 1083509, 973755, 874243, 784115, 702575, 628886, 562364, 502377, 448341, 399719, 356015, 316774, 281577, 250043, 221819, 196586, 174050, 153945, 136027, 120075, 105889, 93287, 82103, 72188, 63408, 55640, 48776, 42716, 37373, 32665, 28522, 24880, 21682, 18876, 16417, 14264, 12382, 10737, 9301, 8050, 6960, 6012, 5187, 4472, 3851, 3313, 2848, 2445, 2098, 1798, 1539, 1316, 1125, 960, 819, 697, 594, 505, 429, 364, 308, 261, 221, 187, 158, 133, 112, 94, 79, 67, 56, 47, 39, 33, 28, 23, 19, 16, 13, 11, 9, 8, 6, 5, 4, 4, 3, 2, 2, 2, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def signed16(x):
    x = x & 65535
    return x - 65536 if x & 32768 else x


def trunc256(x):
    return -((-x)//256) if x < 0 else x//256


def clip16(x):
    return min(32767, max(-32768, x))


def gelu_q8(x):
    a = -x if x < 0 else x
    if a >= 2048:
        return 0 if x < 0 else x
    j = a >> 3
    r = a & 7
    h = (GELU_TAIL_Q30[j]*(8-r) + GELU_TAIL_Q30[j+1]*r)//8
    c = h >> 22
    remainder = h & 4194303
    if remainder > 2097152 or (remainder == 2097152 and (c & 1)):
        c = c + 1
    return -c if x < 0 else x - c


class FalconMLP:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self,
        in_x_0,
        in_x_1,
        in_x_2,
        in_x_3,
        in_up_w_0_0,
        in_up_w_0_1,
        in_up_w_0_2,
        in_up_w_0_3,
        in_up_w_1_0,
        in_up_w_1_1,
        in_up_w_1_2,
        in_up_w_1_3,
        in_up_w_2_0,
        in_up_w_2_1,
        in_up_w_2_2,
        in_up_w_2_3,
        in_up_w_3_0,
        in_up_w_3_1,
        in_up_w_3_2,
        in_up_w_3_3,
        in_down_w_0_0,
        in_down_w_0_1,
        in_down_w_0_2,
        in_down_w_0_3,
        in_down_w_1_0,
        in_down_w_1_1,
        in_down_w_1_2,
        in_down_w_1_3,
        in_down_w_2_0,
        in_down_w_2_1,
        in_down_w_2_2,
        in_down_w_2_3,
        in_down_w_3_0,
        in_down_w_3_1,
        in_down_w_3_2,
        in_down_w_3_3,
        in_up_b_0,
        in_up_b_1,
        in_up_b_2,
        in_up_b_3,
        in_down_b_0,
        in_down_b_1,
        in_down_b_2,
        in_down_b_3,
    ):
        x = [signed16(in_x_0), signed16(in_x_1), signed16(in_x_2), signed16(in_x_3)]
        up_w = [
            [signed16(in_up_w_0_0), signed16(in_up_w_0_1), signed16(in_up_w_0_2), signed16(in_up_w_0_3)],
            [signed16(in_up_w_1_0), signed16(in_up_w_1_1), signed16(in_up_w_1_2), signed16(in_up_w_1_3)],
            [signed16(in_up_w_2_0), signed16(in_up_w_2_1), signed16(in_up_w_2_2), signed16(in_up_w_2_3)],
            [signed16(in_up_w_3_0), signed16(in_up_w_3_1), signed16(in_up_w_3_2), signed16(in_up_w_3_3)],
        ]
        up_b = [signed16(in_up_b_0), signed16(in_up_b_1), signed16(in_up_b_2), signed16(in_up_b_3)]
        down_w = [
            [signed16(in_down_w_0_0), signed16(in_down_w_0_1), signed16(in_down_w_0_2), signed16(in_down_w_0_3)],
            [signed16(in_down_w_1_0), signed16(in_down_w_1_1), signed16(in_down_w_1_2), signed16(in_down_w_1_3)],
            [signed16(in_down_w_2_0), signed16(in_down_w_2_1), signed16(in_down_w_2_2), signed16(in_down_w_2_3)],
            [signed16(in_down_w_3_0), signed16(in_down_w_3_1), signed16(in_down_w_3_2), signed16(in_down_w_3_3)],
        ]
        down_b = [signed16(in_down_b_0), signed16(in_down_b_1), signed16(in_down_b_2), signed16(in_down_b_3)]
        activated = [0]*4
        for j in range(4):
            total = up_b[j]*256
            for i in range(4):
                total = total + x[i]*up_w[j][i]
            activated[j] = gelu_q8(clip16(trunc256(total)))
        y = [0]*4
        for j in range(4):
            total = down_b[j]*256
            for i in range(4):
                total = total + activated[i]*down_w[j][i]
            y[j] = clip16(trunc256(total))
        return {"out_y_0":y[0]&65535,"out_y_1":y[1]&65535,"out_y_2":y[2]&65535,"out_y_3":y[3]&65535}
