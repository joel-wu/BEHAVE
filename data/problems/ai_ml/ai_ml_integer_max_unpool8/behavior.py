# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class MaxUnpool8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_indices):
        result = 0
        for j in range(16):
            value = 0
            for i in range(8):
                if ((in_indices >> (4*i)) & 15) == j:
                    value = (in_data >> (8*i)) & 255
            result |= value << (8*j)
        return {"out_values": result}
