# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class L1NormalizeQ14:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        denominator = 0
        for i in range(8):
            value = s16(in_data >> (16*i))
            denominator += -value if value < 0 else value
        result = 0
        if denominator != 0:
            for i in range(8):
                value = s16(in_data >> (16*i))
                magnitude = -value if value < 0 else value
                quotient = (2*16384*magnitude+denominator)//(2*denominator)
                value = -quotient if value < 0 else quotient
                result |= (value & 65535) << (16*i)
        return {"out_values": result}
