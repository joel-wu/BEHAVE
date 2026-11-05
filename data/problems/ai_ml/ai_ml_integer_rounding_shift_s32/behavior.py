# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s32(x):
    x = x & 4294967295
    return x - 4294967296 if x >= 2147483648 else x

def rdiv(x, exponent):
    mask = (1 << exponent) - 1
    remainder = x & mask
    threshold = (mask >> 1) + (1 if x < 0 else 0)
    return (x >> exponent) + (1 if remainder > threshold else 0)

class RoundingShiftS32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_shift):
        result=0
        for i in range(8):
            value=rdiv(s32(in_data >> (32*i)),in_shift)
            result |= (value & 4294967295) << (32*i)
        return {"out_values": result}
