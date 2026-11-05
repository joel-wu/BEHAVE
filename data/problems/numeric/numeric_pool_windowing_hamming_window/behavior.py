# Periodic cosine-sum window, rounded once to binary32.
WINDOW = (
    0x3DA3D70A, 0x3DEB8D34, 0x3E5BE269, 0x3EBA59B4,
    0x3F0A3D71, 0x3F374E07, 0x3F5D8247, 0x3F77093B,
    0x3F800000, 0x3F77093B, 0x3F5D8247, 0x3F374E07,
    0x3F0A3D71, 0x3EBA59B4, 0x3E5BE269, 0x3DEB8D34,
)


class HammingWindowF32Generator:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_start):
        return [{"out_index": i, "out_coeff": WINDOW[i]} for i in range(16)]
