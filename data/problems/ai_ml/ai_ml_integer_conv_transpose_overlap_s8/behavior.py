# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class ConvTransposeOverlapS8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_bias):
        result=0
        for c in range(2):
            for y in range(5):
                for x in range(5):
                    value=s16(in_bias >> (16*c))
                    for k in range(2):
                        for h in range(2):
                            for w in range(2):
                                i=y-2*h
                                j=x-2*w
                                if 0 <= i and i < 3 and 0 <= j and j < 3:
                                    value += s8(in_data >> (8*(4*k+2*h+w)))*s8(in_weights >> (8*(18*k+9*c+3*i+j)))
                    result |= (value & 4294967295) << (32*(25*c+5*y+x))
        return {"out_values": result}
