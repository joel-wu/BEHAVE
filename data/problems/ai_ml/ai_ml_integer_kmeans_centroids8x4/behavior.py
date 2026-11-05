# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def away_ratio(n,d):
    a=-n if n<0 else n
    q=(2*a+d)//(2*d)
    return -q if n<0 else q

class KMeansCentroids8x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_points, in_labels, in_mask, in_previous):
        result=0
        for cluster in range(4):
            count=0
            for i in range(8):
                if ((in_mask >> i)&1) and ((in_labels >> (2*i))&3)==cluster:
                    count+=1
            result |= count << (256+4*cluster)
            for c in range(4):
                total=0
                for i in range(8):
                    if ((in_mask >> i)&1) and ((in_labels >> (2*i))&3)==cluster:
                        total+=s8(in_points >> (8*(4*i+c)))
                value=away_ratio(total,count) if count else s16(in_previous >> (16*(4*cluster+c)))
                result |= (value & 65535) << (16*(4*cluster+c))
        return {"out_values": result}
