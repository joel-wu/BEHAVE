# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class PReLUPerChannelQ7:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_slopes):
        result = 0
        for i in range(16):
            value = s8(in_data >> (8*i))
            if value < 0:
                a = (in_slopes >> (8*(i%4))) & 255
                value = -((-value*a+64)//128)
                value = max(value,-128)
            result |= (value & 255) << (8*i)
        return {"out_values": result}
