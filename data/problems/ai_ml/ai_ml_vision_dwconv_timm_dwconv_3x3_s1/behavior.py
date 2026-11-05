class Depthwise3x3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def _signed(self, value, width):
        value = value & ((1 << width) - 1)
        return value - (1 << width) if value & (1 << (width - 1)) else value

    def process(self, in_patch, in_kernel, in_bias):
        result = 0
        for channel in range(4):
            total = self._signed(in_bias >> (32 * channel), 32)
            for point in range(9):
                index = 9 * channel + point
                x = self._signed(in_patch >> (8 * index), 8)
                w = self._signed(in_kernel >> (8 * index), 8)
                total = total + x * w
            result = result | ((total & 0xFFFFFFFF) << (32 * channel))
        return {"out_y": result}
