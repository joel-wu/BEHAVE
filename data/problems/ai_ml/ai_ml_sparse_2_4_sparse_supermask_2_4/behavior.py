def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SparseSupermask2of4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_score0,
        in_score1,
        in_score2,
        in_score3,
        in_weight0,
        in_weight1,
        in_weight2,
        in_weight3,
    ):
        s0 = to_unsigned(in_score0, 32)
        s1 = to_unsigned(in_score1, 32)
        s2 = to_unsigned(in_score2, 32)
        s3 = to_unsigned(in_score3, 32)

        if s0 > s1:
            s0, s1 = s1, s0
        if s2 > s3:
            s2, s3 = s3, s2
        if s0 > s2:
            s0, s2 = s2, s0
        if s1 > s3:
            s1, s3 = s3, s1
        if s1 > s2:
            s1, s2 = s2, s1

        k_val = s2

        score0 = to_unsigned(in_score0, 32)
        score1 = to_unsigned(in_score1, 32)
        score2 = to_unsigned(in_score2, 32)
        score3 = to_unsigned(in_score3, 32)

        out_mask0 = 0 if score0 < k_val else 1
        out_mask1 = 0 if score1 < k_val else 1
        out_mask2 = 0 if score2 < k_val else 1
        out_mask3 = 0 if score3 < k_val else 1

        weight0 = to_unsigned(in_weight0, 32)
        weight1 = to_unsigned(in_weight1, 32)
        weight2 = to_unsigned(in_weight2, 32)
        weight3 = to_unsigned(in_weight3, 32)

        return {
            "out_mask0": out_mask0,
            "out_mask1": out_mask1,
            "out_mask2": out_mask2,
            "out_mask3": out_mask3,
            "out_weight0": weight0 if out_mask0 else 0,
            "out_weight1": weight1 if out_mask1 else 0,
            "out_weight2": weight2 if out_mask2 else 0,
            "out_weight3": weight3 if out_mask3 else 0,
        }