from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_sqrt


class AiMlQuantInt8SmoothquantSmoothFactor:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_act_absmax, in_weight_absmax, in_alpha):
        a = in_act_absmax
        w = in_weight_absmax
        if (in_alpha > 16 or (a & 0x7fffffff) >= 0x7f800000 or
                (w & 0x7fffffff) >= 0x7f800000 or
                (a >> 31 and (a & 0x7fffffff) != 0) or
                (w >> 31 and (w & 0x7fffffff) != 0)):
            return {"out_smoothing_factor": 0, "out_error": 1}
        a = fp32_add(a & 0x7fffffff, 0x34000000)
        w = fp32_add(w & 0x7fffffff, 0x34000000)
        if in_alpha == 0:
            result = fp32_div(0x3f800000, w)
        elif in_alpha == 16:
            result = a
        else:
            for _ in range(4):
                a = fp32_sqrt(a)
                w = fp32_sqrt(w)
            numerator = 0x3f800000
            denominator = 0x3f800000
            for k in range(15):
                if k < in_alpha:
                    numerator = fp32_mul(numerator, a)
                if k < 16-in_alpha:
                    denominator = fp32_mul(denominator, w)
            result = fp32_div(numerator, denominator)
        return {"out_smoothing_factor": result, "out_error": 0}
