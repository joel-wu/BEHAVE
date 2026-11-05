# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class DenseBackward2x4x3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_gradient):
        result=0
        for b in range(2):
            for c in range(4):
                value=0
                for o in range(3):
                    value+=s8(in_gradient >> (8*(3*b+o)))*s8(in_weights >> (8*(4*o+c)))
                result |= (value&4294967295) << (32*(4*b+c))
        for o in range(3):
            for c in range(4):
                value=0
                for b in range(2):
                    value+=s8(in_gradient >> (8*(3*b+o)))*s8(in_data >> (8*(4*b+c)))
                result |= (value&4294967295) << (256+32*(4*o+c))
            bias=s8(in_gradient >> (8*o))+s8(in_gradient >> (8*(3+o)))
            result |= (bias&65535) << (640+16*o)
        return {"out_values": result}
