class AbsminNoIdxF32_16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self,
        in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15):
        words = [in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15]
        best = words[0] & 0x7FFFFFFF
        index = 0
        for i in range(1, 16):
            candidate = words[i] & 0x7FFFFFFF
            # Absolute magnitudes are unsigned-ordered except for NaNs.
            if candidate <= 0x7F800000 and best <= 0x7F800000 and candidate < best:
                best = candidate
                index = i
        return {'out_result': best}
