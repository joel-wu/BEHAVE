# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class CompressRows8x2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_mask):
        result = 0
        count = 0
        for i in range(8):
            if ((in_mask >> i) & 1) != 0:
                result |= ((in_data >> (16*i)) & 65535) << (16*count)
                count += 1
        result |= count << 128
        return {"out_values": result}
