# SPDX-License-Identifier: MIT
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class TernaryQuantDense16x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_weights):
        result=0
        for o in range(4):
            scale=0
            for c in range(16):
                w=s8(in_weights >> (8*(16*o+c)))
                magnitude=-w if w<0 else w
                scale=max(scale,magnitude)
            value=0
            for c in range(16):
                w=s8(in_weights >> (8*(16*o+c)))
                magnitude=-w if w<0 else w
                q=0
                if scale>0 and 2*magnitude>=scale:
                    q=-1 if w<0 else 1
                value+=q*s8(in_x >> (8*c))
                result |= (q+1) << (160+2*(16*o+c))
            result |= ((value*scale)&4294967295) << (32*o)
            result |= scale << (128+8*o)
        return {"out_values": result}
