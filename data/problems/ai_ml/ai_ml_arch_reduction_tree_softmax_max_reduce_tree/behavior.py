def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ReductionTreeSoftmaxMaxReduceTree:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7):
        s0 = in_x0 if in_x0 > in_x1 else in_x1
        s1 = in_x2 if in_x2 > in_x3 else in_x3
        s2 = in_x4 if in_x4 > in_x5 else in_x5
        s3 = in_x6 if in_x6 > in_x7 else in_x7
        t0 = s0 if s0 > s1 else s1
        t1 = s2 if s2 > s3 else s3
        out_max = t0 if t0 > t1 else t1
        return {"out_max": to_unsigned(out_max, 8)}