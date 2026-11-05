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

class Forest3Depth2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_features, in_missing, in_node_features, in_thresholds, in_default_left, in_leaves):
        result=0
        total=0
        for tree in range(3):
            node=0
            leaf=0
            for depth in range(2):
                site=3*tree+node
                f=(in_node_features >> (2*site)) & 3
                if (in_missing >> f) & 1:
                    left=(in_default_left >> site) & 1
                else:
                    left=1 if s8(in_features >> (8*f))<=s8(in_thresholds >> (8*site)) else 0
                branch=0 if left else 1
                leaf=2*leaf+branch
                node=1+branch
            value=s16(in_leaves >> (16*(4*tree+leaf)))
            result |= (value & 65535) << (16*tree)
            total+=value
        result |= (away_ratio(total,3) & 65535) << 48
        return {"out_values": result}
