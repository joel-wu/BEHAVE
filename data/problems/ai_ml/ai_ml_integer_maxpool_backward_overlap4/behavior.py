# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class MaxPoolBackwardOverlap4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_grad):
        dx=[0]*16
        for h in range(2):
            for w in range(2):
                best=-129
                index=0
                for kh in range(3):
                    for kw in range(3):
                        j=4*(h+kh)+w+kw
                        value=s8(in_x >> (8*j))
                        if value>best:
                            best=value
                            index=j
                dx[index]+=s16(in_grad >> (16*(2*h+w)))
        result=0
        for i in range(16):
            result |= (dx[i]&4294967295) << (32*i)
        return {"out_values": result}
