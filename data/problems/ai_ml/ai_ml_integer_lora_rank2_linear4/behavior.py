# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class LoRARank2Linear4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_w, in_a, in_b):
        hidden=[0]*2
        for r in range(2):
            for c in range(4):
                hidden[r]+=s8(in_a >> (8*(4*r+c)))*s8(in_x >> (8*c))
        result=0
        for o in range(4):
            value=0
            for c in range(4):
                value+=s8(in_w >> (8*(4*o+c)))*s8(in_x >> (8*c))
            for r in range(2):
                value+=s8(in_b >> (8*(2*o+r)))*hidden[r]
            result |= (value&4294967295) << (32*o)
        return {"out_values": result}
