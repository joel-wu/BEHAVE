# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s32(x):
    x = x & 4294967295
    return x - 4294967296 if x >= 2147483648 else x

def highmul(a, b):
    if a == -2147483648 and b == -2147483648:
        return 2147483647
    product = a * b
    nudge = 1073741824 if product >= 0 else -1073741823
    adjusted = product + nudge
    if adjusted < 0:
        return -((-adjusted) // 2147483648)
    return adjusted // 2147483648

def rdiv(x, exponent):
    mask = (1 << exponent) - 1
    remainder = x & mask
    threshold = (mask >> 1) + (1 if x < 0 else 0)
    return (x >> exponent) + (1 if remainder > threshold else 0)

def clip8(x):
    if x < -128:
        return -128
    if x > 127:
        return 127
    return x

class RequantizeS32S8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_multiplier, in_shift, in_zero):
        result=0
        for i in range(4):
            value=highmul(s32(in_data >> (32*i)),in_multiplier)
            value=clip8(rdiv(value,in_shift)+s8(in_zero))
            result |= (value & 255) << (8*i)
        return {"out_values": result}
