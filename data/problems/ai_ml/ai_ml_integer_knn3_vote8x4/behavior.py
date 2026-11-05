# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class KNN3Vote8x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_points, in_query, in_labels, in_mask):
        distances=[0]*8
        for i in range(8):
            distance=0
            for c in range(4):
                d=s8(in_points >> (8*(4*i+c)))-s8(in_query >> (8*c))
                distance+=d*d
            distances[i]=distance
        used=0
        votes=[0]*4
        result=0
        count=0
        for rank in range(3):
            best=0
            distance=1048576
            found=0
            for i in range(8):
                if ((in_mask >> i)&1) and not ((used >> i)&1) and distances[i]<distance:
                    best=i
                    distance=distances[i]
                    found=1
            if found:
                used |= 1 << best
                label=(in_labels >> (2*best)) & 3
                votes[label]+=1
                result |= best << (3*rank)
                result |= distance << (25+18*rank)
                count+=1
        result |= count << 9
        winner=0
        for c in range(4):
            result |= votes[c] << (11+3*c)
            if votes[c]>votes[winner]:
                winner=c
        result |= winner << 23
        return {"out_values": result}
