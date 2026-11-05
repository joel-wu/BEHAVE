# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class ConvWeightBackward2x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_grad):
        result=0
        for o in range(2):
            for c in range(2):
                for kh in range(3):
                    for kw in range(3):
                        value=0
                        for h in range(2):
                            for w in range(2):
                                value+=s8(in_grad >> (8*(4*o+2*h+w)))*s8(in_x >> (8*(16*c+4*(h+kh)+w+kw)))
                        result |= (value & 4294967295) << (32*(18*o+9*c+3*kh+kw))
            value=0
            for j in range(4):
                value+=s8(in_grad >> (8*(4*o+j)))
            result |= (value & 65535) << (1152+16*o)
        return {"out_values": result}
