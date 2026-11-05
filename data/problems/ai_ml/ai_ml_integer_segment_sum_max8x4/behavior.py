# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class SegmentSumMax8x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_segments, in_mask):
        result=0
        for s in range(4):
            count=0
            for i in range(8):
                if ((in_mask >> i) & 1) and ((in_segments >> (2*i)) & 3)==s:
                    count+=1
            result |= count << (100*s+96)
            for c in range(4):
                total=0
                peak=-128
                for i in range(8):
                    if ((in_mask >> i) & 1) and ((in_segments >> (2*i)) & 3)==s:
                        sample=s8(in_data >> (8*(4*i+c)))
                        total+=sample
                        peak=max(peak,sample)
                if count==0:
                    peak=0
                result |= (total & 65535) << (100*s+16*c)
                result |= (peak & 255) << (100*s+64+8*c)
        return {"out_values": result}
