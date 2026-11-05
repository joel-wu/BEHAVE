# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class HardSwishQ8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result = 0
        for i in range(8):
            value = s16(in_data >> (16*i))
            gate = min(1536,max(0,value+768))
            product = value*gate
            value = -((-product+768)//1536) if product < 0 else (product+768)//1536
            result |= (value & 65535) << (16*i)
        return {"out_values": result}
