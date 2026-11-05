def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RadixSortMultipass8b:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = None

    def process(
        self,
        in_key_0,
        in_key_1,
        in_key_2,
        in_key_3,
        in_key_4,
        in_key_5,
        in_key_6,
        in_key_7,
    ):
        keys = [in_key_0 & 4294967295, in_key_1 & 4294967295, in_key_2 & 4294967295, in_key_3 & 4294967295, in_key_4 & 4294967295, in_key_5 & 4294967295, in_key_6 & 4294967295, in_key_7 & 4294967295]
        order = [0]*8
        for i in range(8):
            rank = 0
            for j in range(8):
                if keys[j] < keys[i] or (keys[j] == keys[i] and j < i):
                    rank += 1
            order[rank] = i

        return {
            "out_rank_0": to_unsigned(order[0], 3),
            "out_rank_1": to_unsigned(order[1], 3),
            "out_rank_2": to_unsigned(order[2], 3),
            "out_rank_3": to_unsigned(order[3], 3),
            "out_rank_4": to_unsigned(order[4], 3),
            "out_rank_5": to_unsigned(order[5], 3),
            "out_rank_6": to_unsigned(order[6], 3),
            "out_rank_7": to_unsigned(order[7], 3),
        }
