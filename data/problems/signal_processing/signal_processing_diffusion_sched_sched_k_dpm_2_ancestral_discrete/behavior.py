def signed(x):
    return x-4294967296 if x >= 2147483648 else x


def sat32(x):
    return min(2147483647,max(-2147483648,x)) & 0xffffffff


class SignalProcessingDiffusionSchedSchedKDpm2AncestralDiscrete:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = 0
        self.saved_sample = 0
        self.sigma = 0
        self.interpol = 0
        self.down = 0
        self.up = 0

    def process(self, in_phase, in_model_output, in_sample, in_noise,
                in_sigma, in_sigma_interpol, in_sigma_down, in_sigma_up):
        error = 0
        previous = 0
        predicted = 0
        model = signed(in_model_output)
        sample = signed(in_sample)
        noise = signed(in_noise)
        if in_phase != self.pending:
            error = 1
        elif in_phase == 0:
            if in_sigma == 0 or in_sigma_interpol == 0 or in_sigma_interpol > in_sigma or in_sigma_down > in_sigma_interpol:
                error = 2
            else:
                self.saved_sample = sample
                self.sigma = in_sigma
                self.interpol = in_sigma_interpol
                self.down = in_sigma_down
                self.up = in_sigma_up
                predicted = sat32((sample*65536-in_sigma*model)//65536)
                previous = sat32((sample*65536+model*(in_sigma_interpol-in_sigma))//65536)
                self.pending = 1
        else:
            predicted = sat32((sample*65536-self.interpol*model)//65536)
            previous = sat32((self.saved_sample*65536+model*(self.down-self.sigma)+noise*self.up)//65536)
            self.pending = 0
        return {"out_prev_sample":previous,"out_pred_original_sample":predicted,"out_error":error}
