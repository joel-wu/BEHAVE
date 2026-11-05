# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class Depthwise3x3S8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_bias):
        result = 0
        for h in range(2):
            for w in range(2):
                for c in range(2):
                    value = s16(in_bias >> (16*c))
                    for i in range(3):
                        for j in range(3):
                            x = s8(in_data >> (8*((4*(h+i)+w+j)*2+c)))
                            weight = s8(in_weights >> (8*((3*i+j)*2+c)))
                            value += x*weight
                    result |= (value & 4294967295) << (32*((2*h+w)*2+c))
        return {"out_values": result}
