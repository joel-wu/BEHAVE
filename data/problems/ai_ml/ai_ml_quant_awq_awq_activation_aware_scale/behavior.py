from fp_lib import fp32_div

class AWQActivationAwareScale:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_equalization_scale):
        return {"out_act_pre_scale": fp32_div(0x3F800000, in_equalization_scale)}