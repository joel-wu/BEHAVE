# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
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

class Q31SaturatingHighMul:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b):
        result=0
        for i in range(4):
            value=highmul(s32(in_a >> (32*i)),s32(in_b >> (32*i)))
            result |= (value & 4294967295) << (32*i)
        return {"out_values": result}
