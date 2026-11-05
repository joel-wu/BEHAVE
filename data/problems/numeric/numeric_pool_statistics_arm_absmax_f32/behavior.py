class AbsMaxF32:
    def __init__(self):
        self.src = [0] * 16

    def reset(self):
        # Reset does not clear the externally supplied samples.
        pass

    def process(self, in_base_addr):
        words = [self.src[(in_base_addr + i) & 15] for i in range(16)]
        best = words[0] & 0x7FFFFFFF
        index = 0
        for i in range(1, 16):
            candidate = words[i] & 0x7FFFFFFF
            # Absolute magnitudes are unsigned-ordered except for NaNs.
            if candidate <= 0x7F800000 and best <= 0x7F800000 and candidate > best:
                best = candidate
                index = i
        return {'out_result': best, 'out_index': index}
