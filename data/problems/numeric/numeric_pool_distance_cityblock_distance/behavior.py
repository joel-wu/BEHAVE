from fp_lib import fp32_abs, fp32_add, fp32_sub


class CityblockDistanceF32x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3,
        in_b0, in_b1, in_b2, in_b3,
    ):
        d0 = fp32_abs(fp32_sub(in_a0, in_b0))
        d1 = fp32_abs(fp32_sub(in_a1, in_b1))
        d2 = fp32_abs(fp32_sub(in_a2, in_b2))
        d3 = fp32_abs(fp32_sub(in_a3, in_b3))
        s0 = fp32_add(d0, d1)
        s1 = fp32_add(s0, d2)
        out_distance = fp32_add(s1, d3)
        return {"out_distance": out_distance}