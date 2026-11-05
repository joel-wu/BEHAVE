# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class ScatterAdd16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_indices, in_updates):
        result=0
        for j in range(16):
            value=s16(in_data >> (16*j))
            for i in range(8):
                if ((in_indices >> (4*i)) & 15) == j:
                    value += s16(in_updates >> (16*i))
            result |= (value & 65535) << (16*j)
        return {"out_values": result}
