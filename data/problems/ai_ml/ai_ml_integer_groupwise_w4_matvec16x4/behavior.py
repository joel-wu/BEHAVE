# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def s4(x):
    x = x & 15
    return x-16 if x >= 8 else x

class GroupwiseW4MatVec16x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_zero_x, in_zero_w, in_scales, in_bias):
        result=0
        for o in range(4):
            total=s16(in_bias >> (16*o))
            for g in range(4):
                value=0
                zero=s4(in_zero_w >> (4*(4*o+g)))
                scale=1+((in_scales >> (4*(4*o+g))) & 15)
                for k in range(4):
                    x=s8(in_data >> (8*(4*g+k)))-s8(in_zero_x)
                    w=s4(in_weights >> (4*(16*o+4*g+k)))-zero
                    value+=x*w
                total+=value*scale
            result |= (total & 4294967295) << (32*o)
        return {"out_values": result}
