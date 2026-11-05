# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class PackedS4S8Dot16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_weights, in_data, in_bias):
        result=s16(in_bias)
        for i in range(16):
            weight=(in_weights >> (4*i)) & 15
            if weight >= 8:
                weight -= 16
            result += weight*s8(in_data >> (8*i))
        result &= 4294967295
        return {"out_values": result}
