from fp_lib import fp32_add, fp32_sub, fp32_mul, fp32_div, fp32_sqrt


FP32_ONE = 0x3F800000
FP32_FOUR = 0x40800000


class CorrelationDistanceF32Vec4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3,
        in_b0, in_b1, in_b2, in_b3
    ):
        sum_a = fp32_add(fp32_add(fp32_add(in_a0, in_a1), in_a2), in_a3)
        sum_b = fp32_add(fp32_add(fp32_add(in_b0, in_b1), in_b2), in_b3)

        mean_a = fp32_div(sum_a, FP32_FOUR)
        mean_b = fp32_div(sum_b, FP32_FOUR)

        ca0 = fp32_sub(in_a0, mean_a)
        ca1 = fp32_sub(in_a1, mean_a)
        ca2 = fp32_sub(in_a2, mean_a)
        ca3 = fp32_sub(in_a3, mean_a)

        cb0 = fp32_sub(in_b0, mean_b)
        cb1 = fp32_sub(in_b1, mean_b)
        cb2 = fp32_sub(in_b2, mean_b)
        cb3 = fp32_sub(in_b3, mean_b)

        ca0_sq = fp32_mul(ca0, ca0)
        ca1_sq = fp32_mul(ca1, ca1)
        ca2_sq = fp32_mul(ca2, ca2)
        ca3_sq = fp32_mul(ca3, ca3)

        cb0_sq = fp32_mul(cb0, cb0)
        cb1_sq = fp32_mul(cb1, cb1)
        cb2_sq = fp32_mul(cb2, cb2)
        cb3_sq = fp32_mul(cb3, cb3)

        dot0 = fp32_mul(ca0, cb0)
        dot1 = fp32_mul(ca1, cb1)
        dot2 = fp32_mul(ca2, cb2)
        dot3 = fp32_mul(ca3, cb3)

        pwra = fp32_add(fp32_add(fp32_add(ca0_sq, ca1_sq), ca2_sq), ca3_sq)
        pwrb = fp32_add(fp32_add(fp32_add(cb0_sq, cb1_sq), cb2_sq), cb3_sq)
        dot = fp32_add(fp32_add(fp32_add(dot0, dot1), dot2), dot3)

        dot_avg = fp32_div(dot, FP32_FOUR)
        pwra_avg = fp32_div(pwra, FP32_FOUR)
        pwrb_avg = fp32_div(pwrb, FP32_FOUR)

        power_product = fp32_mul(pwra_avg, pwrb_avg)
        denominator = fp32_sqrt(power_product)
        correlation = fp32_div(dot_avg, denominator)
        out_distance = fp32_sub(FP32_ONE, correlation)

        return {"out_distance": out_distance}