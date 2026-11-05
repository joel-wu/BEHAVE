from fp_lib import fp32_add, fp32_sub, fp32_mul, fp32_div, fp32_sqrt


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BatchNorm2dInference:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x0, in_x1, in_x2, in_x3,
        in_weight0, in_weight1, in_weight2, in_weight3,
        in_bias0, in_bias1, in_bias2, in_bias3,
        in_running_mean0, in_running_mean1, in_running_mean2, in_running_mean3,
        in_running_var0, in_running_var1, in_running_var2, in_running_var3,
        in_eps
    ):
        one = 0x3F800000

        delta0 = fp32_sub(in_x0, in_running_mean0)
        var_eps0 = fp32_add(in_running_var0, in_eps)
        inv_std0 = fp32_div(one, fp32_sqrt(var_eps0))
        scaled0 = fp32_mul(fp32_mul(delta0, inv_std0), in_weight0)
        out_y0 = fp32_add(scaled0, in_bias0)

        delta1 = fp32_sub(in_x1, in_running_mean1)
        var_eps1 = fp32_add(in_running_var1, in_eps)
        inv_std1 = fp32_div(one, fp32_sqrt(var_eps1))
        scaled1 = fp32_mul(fp32_mul(delta1, inv_std1), in_weight1)
        out_y1 = fp32_add(scaled1, in_bias1)

        delta2 = fp32_sub(in_x2, in_running_mean2)
        var_eps2 = fp32_add(in_running_var2, in_eps)
        inv_std2 = fp32_div(one, fp32_sqrt(var_eps2))
        scaled2 = fp32_mul(fp32_mul(delta2, inv_std2), in_weight2)
        out_y2 = fp32_add(scaled2, in_bias2)

        delta3 = fp32_sub(in_x3, in_running_mean3)
        var_eps3 = fp32_add(in_running_var3, in_eps)
        inv_std3 = fp32_div(one, fp32_sqrt(var_eps3))
        scaled3 = fp32_mul(fp32_mul(delta3, inv_std3), in_weight3)
        out_y3 = fp32_add(scaled3, in_bias3)

        return {
            "out_y0": to_unsigned(out_y0, 32),
            "out_y1": to_unsigned(out_y1, 32),
            "out_y2": to_unsigned(out_y2, 32),
            "out_y3": to_unsigned(out_y3, 32),
        }