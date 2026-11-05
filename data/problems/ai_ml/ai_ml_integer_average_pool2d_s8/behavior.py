# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class AveragePool2DS8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result = 0
        for h in range(2):
            for w in range(2):
                for c in range(2):
                    value = 0
                    for i in range(2):
                        for j in range(2):
                            sample = s8(in_data >> (8*((4*(2*h+i)+2*w+j)*2+c)))
                            value += sample
                    value = -((-value + 2) // 4) if value < 0 else (value + 2) // 4
                    result |= (value & 255) << (8*((2*h+w)*2+c))
        return {"out_values": result}
