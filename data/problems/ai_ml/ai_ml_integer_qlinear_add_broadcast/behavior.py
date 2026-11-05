# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def clip8(x):
    if x < -128:
        return -128
    if x > 127:
        return 127
    return x

def rne(x, shift):
    d = 1 << shift
    q = x // d
    r = x - q*d
    return q + (1 if 2*r > d or (2*r == d and q % 2 != 0) else 0)

class QLinearAddBroadcast:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b, in_zero_a, in_zero_b, in_zero_y, in_shift_a, in_shift_b):
        result=0
        for r in range(2):
            for c in range(4):
                a=s8(in_a >> (8*(4*r+c)))-s8(in_zero_a)
                b=s8(in_b >> (8*c))-s8(in_zero_b)
                shift=max(in_shift_a,in_shift_b)
                value=(a << (shift-in_shift_a))+(b << (shift-in_shift_b))
                value=clip8(rne(value,shift)+s8(in_zero_y))
                result |= (value & 255) << (8*(4*r+c))
        return {"out_values": result}
