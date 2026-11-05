def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


GAIN_TABLE = [1, 2, 4, 8, 16]
BIT_DEPTH = 10
MAX_PIXEL = (1 << BIT_DEPTH) - 1
CURRENT_GAIN_RESET = 0


class BayerDigitalGain:
    def __init__(self):
        self.reset()

    def reset(self):
        self.current_gain = CURRENT_GAIN_RESET

    def process(self, in_pixel, in_is_auto, in_ae_feedback):
        current_gain = self.current_gain
        ae_feedback = to_signed(in_ae_feedback, 2)

        if in_is_auto:
            if ae_feedback < 0:
                selected_gain_index = current_gain + 1
                if selected_gain_index > len(GAIN_TABLE) - 1:
                    selected_gain_index = len(GAIN_TABLE) - 1
            elif ae_feedback > 0:
                selected_gain_index = current_gain - 1
                if selected_gain_index < 0:
                    selected_gain_index = 0
            else:
                selected_gain_index = current_gain
        else:
            selected_gain_index = current_gain

        self.current_gain = selected_gain_index

        gain_value = GAIN_TABLE[selected_gain_index]
        product = in_pixel * gain_value
        if product < 0:
            out_pixel = 0
        elif product > MAX_PIXEL:
            out_pixel = MAX_PIXEL
        else:
            out_pixel = product

        return {
            "out_pixel": to_unsigned(out_pixel, 16),
            "out_current_gain": to_unsigned(selected_gain_index, 3),
        }