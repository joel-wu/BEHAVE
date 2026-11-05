from fp_lib import fp32_add


class ReductionTreePairwiseReduceFp:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7):
        s0 = fp32_add(in_x0, in_x1)
        s1 = fp32_add(in_x2, in_x3)
        s2 = fp32_add(in_x4, in_x5)
        s3 = fp32_add(in_x6, in_x7)
        t0 = fp32_add(s0, s1)
        t1 = fp32_add(s2, s3)
        return {"out_y": fp32_add(t0, t1)}