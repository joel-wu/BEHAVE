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

def s9(x):
    x = x & 511
    return x-512 if x >= 256 else x

class ProjectionResidualS8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b, in_weights, in_bias, in_offset_a, in_offset_b, in_mult_a, in_mult_b, in_mult_out, in_shift_a, in_shift_b, in_shift_out, in_left_shift, in_zero_out):
        result=0
        for i in range(4):
            a=s16(in_bias >> (16*i))
            for j in range(4):
                a += s8(in_weights >> (8*(4*i+j)))*(s8(in_a >> (8*j))+s9(in_offset_a))
            a *= 1 << in_left_shift
            b=(s8(in_b >> (8*i))+s9(in_offset_b))*(1 << in_left_shift)
            a=rdiv(highmul(a,in_mult_a),in_shift_a)
            b=rdiv(highmul(b,in_mult_b),in_shift_b)
            value=clip8(rdiv(highmul(a+b,in_mult_out),in_shift_out)+s8(in_zero_out))
            result |= (value & 255) << (8*i)
        return {"out_values": result}
