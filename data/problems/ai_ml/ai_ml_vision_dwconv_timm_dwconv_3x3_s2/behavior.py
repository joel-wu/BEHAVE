def signed8(word):
    value = word & 255
    return value - 256 if value >= 128 else value


class AiMlVisionDwconvTimmDwconv3x3S2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_weight):
        result = 0
        for oy in range(2):
            for ox in range(2):
                for channel in range(3):
                    total = 0
                    for ky in range(3):
                        for kx in range(3):
                            iy = 2 * oy + ky - 1
                            ix = 2 * ox + kx - 1
                            if 0 <= iy and iy < 4 and 0 <= ix and ix < 4:
                                source = ((iy * 4 + ix) * 3 + channel) * 8
                                weight = ((channel * 3 + ky) * 3 + kx) * 8
                                total += signed8(in_x >> source) * signed8(in_weight >> weight)
                    lane = (oy * 2 + ox) * 3 + channel
                    result |= (total & 1048575) << (20 * lane)
        return {"out_y": result}
