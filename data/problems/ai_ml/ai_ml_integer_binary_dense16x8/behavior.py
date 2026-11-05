# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class BinaryDense16x8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_thresholds):
        result=0
        for o in range(8):
            score=-16
            for k in range(16):
                if ((in_data >> k) & 1)==((in_weights >> (16*o+k)) & 1):
                    score+=2
            result |= (score & 255) << (8*o)
            if score>=s8(in_thresholds >> (8*o)):
                result |= 1 << (64+o)
        return {"out_values": result}
