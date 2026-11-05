# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def clip8(x):
    if x < -128:
        return -128
    if x > 127:
        return 127
    return x

def rne(x, shift):
    d = 1 << shift
    q = x // d
    r = x - q*d
    return q + (1 if 2*r > d or (2*r == d and q % 2 != 0) else 0)

class QLinearConv3x3PerChannel:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_bias, in_zero_x, in_zero_w, in_shifts):
        result=0
        for o in range(3):
            for h in range(2):
                for w in range(2):
                    value=s16(in_bias >> (16*o))
                    for c in range(2):
                        for i in range(3):
                            for j in range(3):
                                x=s8(in_data >> (8*(16*c+4*(h+i)+w+j)))-s8(in_zero_x)
                                weight=s8(in_weights >> (8*(18*o+9*c+3*i+j)))-s8(in_zero_w >> (8*o))
                                value+=x*weight
                    value=clip8(rne(value,(in_shifts >> (4*o)) & 15))
                    result |= (value & 255) << (8*(4*o+2*h+w))
        return {"out_values": result}
