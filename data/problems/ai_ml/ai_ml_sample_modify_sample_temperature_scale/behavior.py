from fp_lib import fp32_cmp, fp32_div


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlSampleModifySampleTemperatureScale:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_logit0,
        in_logit1,
        in_logit2,
        in_logit3,
        in_temperature,
        in_all_random,
        in_greedy_token,
        in_random_token,
    ):
        eps = 0x3727C5AC
        one = 0x3F800000
        cmp = fp32_cmp(in_temperature, eps)
        temp_lt_eps = 1 if cmp == -1 else 0
        temp_used = one if ((in_all_random & 1) == 0 and temp_lt_eps == 1) else in_temperature
        out_sampled_token = in_greedy_token if ((in_all_random & 1) == 0 and temp_lt_eps == 1) else in_random_token
        return {
            "out_logit0": to_unsigned(fp32_div(in_logit0, temp_used), 32),
            "out_logit1": to_unsigned(fp32_div(in_logit1, temp_used), 32),
            "out_logit2": to_unsigned(fp32_div(in_logit2, temp_used), 32),
            "out_logit3": to_unsigned(fp32_div(in_logit3, temp_used), 32),
            "out_sampled_token": to_unsigned(out_sampled_token, 2),
        }
