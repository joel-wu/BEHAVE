def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def pair_less(a_val, a_id, b_val, b_id):
    return (a_val < b_val) or ((a_val == b_val) and (a_id < b_id))


class DataSystemsSortRankMinheapPop:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_k,
        in_val_0, in_val_1, in_val_2, in_val_3,
        in_val_4, in_val_5, in_val_6, in_val_7,
        in_id_0, in_id_1, in_id_2, in_id_3,
        in_id_4, in_id_5, in_id_6, in_id_7,
    ):
        k = to_unsigned(in_k, 4)
        vals = [
            to_unsigned(in_val_0, 32), to_unsigned(in_val_1, 32),
            to_unsigned(in_val_2, 32), to_unsigned(in_val_3, 32),
            to_unsigned(in_val_4, 32), to_unsigned(in_val_5, 32),
            to_unsigned(in_val_6, 32), to_unsigned(in_val_7, 32),
        ]
        ids = [
            to_unsigned(in_id_0, 64), to_unsigned(in_id_1, 64),
            to_unsigned(in_id_2, 64), to_unsigned(in_id_3, 64),
            to_unsigned(in_id_4, 64), to_unsigned(in_id_5, 64),
            to_unsigned(in_id_6, 64), to_unsigned(in_id_7, 64),
        ]

        error = not (1 <= k <= 8)
        if not error:
            for child in range(1, k):
                parent = (child - 1) // 2
                if pair_less(vals[child], ids[child], vals[parent], ids[parent]):
                    error = True

        if not error and k >= 2:
            last_val = vals[k - 1]
            last_id = ids[k - 1]
            i = 1
            while True:
                i1 = i << 1
                i2 = i1 + 1
                if i1 > k:
                    break
                if i2 > k:
                    c = i1
                elif pair_less(vals[i1 - 1], ids[i1 - 1], vals[i2 - 1], ids[i2 - 1]):
                    c = i1
                else:
                    c = i2
                if pair_less(last_val, last_id, vals[c - 1], ids[c - 1]):
                    break
                vals[i - 1] = vals[c - 1]
                ids[i - 1] = ids[c - 1]
                i = c
            vals[i - 1] = last_val
            ids[i - 1] = last_id

        return {
            "out_error": 1 if error else 0,
            "out_val_0": to_unsigned(vals[0], 32),
            "out_val_1": to_unsigned(vals[1], 32),
            "out_val_2": to_unsigned(vals[2], 32),
            "out_val_3": to_unsigned(vals[3], 32),
            "out_val_4": to_unsigned(vals[4], 32),
            "out_val_5": to_unsigned(vals[5], 32),
            "out_val_6": to_unsigned(vals[6], 32),
            "out_val_7": to_unsigned(vals[7], 32),
            "out_id_0": to_unsigned(ids[0], 64),
            "out_id_1": to_unsigned(ids[1], 64),
            "out_id_2": to_unsigned(ids[2], 64),
            "out_id_3": to_unsigned(ids[3], 64),
            "out_id_4": to_unsigned(ids[4], 64),
            "out_id_5": to_unsigned(ids[5], 64),
            "out_id_6": to_unsigned(ids[6], 64),
            "out_id_7": to_unsigned(ids[7], 64),
        }
