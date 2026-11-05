# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class WeightedEmbeddingBags2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_table, in_indices, in_weights, in_lengths):
        result=0
        for b in range(2):
            length=min(4,(in_lengths >> (3*b))&7)
            for c in range(4):
                value=0
                for j in range(4):
                    if j<length:
                        slot=4*b+j
                        row=(in_indices >> (3*slot))&7
                        value+=s8(in_weights >> (8*slot))*s8(in_table >> (8*(4*row+c)))
                result |= (value&4294967295) << (32*(4*b+c))
        return {"out_values": result}
