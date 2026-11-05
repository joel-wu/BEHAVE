def signed32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def trunc_div(value, denominator):
    q = abs(value) // denominator
    return -q if value < 0 else q


class SignalProcessingDiffusionSchedSchedLmsDiscrete:
    def __init__(self):
        self.reset()

    def reset(self):
        self.initialized = 0
        self.step_index = 0
        self.derivatives = [0] * 4
        self.count = 0

    def process(self, in_model_output, in_timestep, in_sample, in_order):
        index = self.step_index if self.initialized else min(in_timestep & 0xFFFFFFFF, 1000)
        sample = signed32(in_sample)
        noise = signed32(in_model_output)
        previous = sample
        predicted = sample
        if index < 1000:
            sigma = 1000 - index
            predicted = sample - sigma * noise
            history = [noise] + self.derivatives[:3]
            count = min(self.count + 1, 4)
            order = min(max(in_order & 7, 1), 4, count)
            if order == 1:
                correction = -history[0]
            elif order == 2:
                correction = trunc_div(-3 * history[0], 2) + trunc_div(history[1], 2)
            elif order == 3:
                correction = (trunc_div(-23 * history[0], 12)
                              + trunc_div(16 * history[1], 12)
                              + trunc_div(-5 * history[2], 12))
            else:
                correction = (trunc_div(-55 * history[0], 24)
                              + trunc_div(59 * history[1], 24)
                              + trunc_div(-37 * history[2], 24)
                              + trunc_div(9 * history[3], 24))
            previous = sample + correction
            self.derivatives = history
            self.count = count
            index += 1
        self.step_index = index
        self.initialized = 1
        return {"out_prev_sample": previous & 0xFFFFFFFF,
                "out_pred_original_sample": predicted & 0xFFFFFFFF}
