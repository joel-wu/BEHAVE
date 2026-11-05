# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def away(n, d):
    return -((-n+d//2)//d) if n < 0 else (n+d//2)//d

class LocalResponseNormQ8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result=0
        for c in range(5):
            for p in range(4):
                denom=16384
                for j in range(5):
                    if c-1 <= j and j <= c+1:
                        sample=s8(in_data >> (8*(4*j+p)))
                        denom += sample*sample
                value=away(4194304*s8(in_data >> (8*(4*c+p))),denom)
                result |= (value & 65535) << (16*(4*c+p))
        return {"out_values": result}
