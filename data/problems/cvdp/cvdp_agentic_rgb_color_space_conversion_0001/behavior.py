def maximum(rgb):
    r = (rgb >> 16) & 255
    g = (rgb >> 8) & 255
    b = rgb & 255
    a = r if r >= g else g
    return a if a >= b else b


def delta(rgb):
    r = (rgb >> 16) & 255
    g = (rgb >> 8) & 255
    b = rgb & 255
    a = r if r <= g else g
    minimum = a if a <= b else b
    return maximum(rgb) - minimum


class RgbColorSpaceHsv:
    def __init__(self):
        self.reset()

    def reset(self):
        self.reciprocal_table = [0] * 256
        self.rgb_pipe = [0] * 4
        self.results = [0] * 4
        self.valid_pipe = 0
        self.inverse_max = 0
        self.inverse_delta = 0
        self.saturation = 0

    def process(self, rst, we, waddr, wdata, valid_in, r_component, g_component, b_component):
        if rst:
            self.rgb_pipe = [0] * 4
            self.results = [0] * 4
            self.valid_pipe = 0
            self.inverse_max = 0
            self.inverse_delta = 0
            self.saturation = 0
        valid_out = (self.valid_pipe >> 7) & 1
        word_out = self.results[3] if valid_out else 0
        out = {"h_component": (word_out >> 25) & 4095,
               "s_component": (word_out >> 12) & 8191,
               "v_component": word_out & 4095, "valid_out": valid_out}
        if rst == 0:
            rgb = self.rgb_pipe[3]
            r = (rgb >> 16) & 255
            g = (rgb >> 8) & 255
            b = rgb & 255
            if r >= g and r >= b:
                difference = g - b
                offset = 0
            elif g >= b:
                difference = b - r
                offset = 480
            else:
                difference = r - g
                offset = 960
            hue = 0
            if delta(rgb) != 0:
                hue = (((60 * difference * self.inverse_delta) >> 22) + offset) % 1440
            word = (hue << 25) | (self.saturation << 12) | maximum(rgb)
            next_saturation = 0
            if maximum(self.rgb_pipe[2]) != 0:
                next_saturation = ((delta(self.rgb_pipe[2]) * self.inverse_max + 2048) >> 12) & 8191
            self.inverse_max = self.reciprocal_table[maximum(self.rgb_pipe[1])]
            self.inverse_delta = self.reciprocal_table[delta(self.rgb_pipe[2])]
            self.saturation = next_saturation
            for i in range(3):
                self.rgb_pipe[3-i] = self.rgb_pipe[2-i]
                self.results[3-i] = self.results[2-i]
            self.rgb_pipe[0] = (r_component << 16) | (g_component << 8) | b_component
            self.results[0] = word
            self.valid_pipe = ((self.valid_pipe << 1) | valid_in) & 255
        if we:
            self.reciprocal_table[waddr] = wdata
        return out
