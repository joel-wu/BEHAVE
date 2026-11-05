from fp_lib import fp32_add


class Fp32VectorAccumulate4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        sum0 = fp32_add(in_x0, in_x1)
        sum1 = fp32_add(sum0, in_x2)
        sum2 = fp32_add(sum1, in_x3)
        return {"out_sum": sum2}