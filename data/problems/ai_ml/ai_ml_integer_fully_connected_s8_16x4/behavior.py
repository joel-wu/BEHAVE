# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

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

class FullyConnectedS8_16x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_weights, in_bias, in_multipliers, in_shifts, in_zero):
        result=0
        for r in range(4):
            value=s16(in_bias >> (16*r))
            for c in range(16):
                value += s8(in_weights >> (8*(16*r+c)))*s8(in_data >> (8*c))
            multiplier=(in_multipliers >> (31*r)) & 2147483647
            shift=(in_shifts >> (4*r)) & 15
            value=clip8(rdiv(highmul(value,multiplier),shift)+s8(in_zero))
            result |= (value & 255) << (8*r)
        return {"out_values": result}
