# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class EmbeddingGather8x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_table, in_indices):
        result=0
        for i in range(4):
            row=(in_indices >> (3*i)) & 7
            for c in range(4):
                value=(in_table >> (8*(4*row+c))) & 255
                result |= value << (8*(4*i+c))
        return {"out_values": result}
