# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class Col2ImOverlap3x3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_columns):
        result = 0
        for y in range(3):
            for x in range(3):
                value = 0
                for h in range(2):
                    for w in range(2):
                        i = y-h
                        j = x-w
                        if 0 <= i and i < 2 and 0 <= j and j < 2:
                            value += s8(in_columns >> (8*(4*(2*i+j)+2*h+w)))
                result |= (value & 65535) << (16*(3*y+x))
        return {"out_values": result}
