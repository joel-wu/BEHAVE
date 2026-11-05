# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def round_even_ratio(n, d):
    a = -n if n < 0 else n
    q = a // d
    rem = a % d
    if 2 * rem > d or (2 * rem == d and (q & 1) != 0):
        q += 1
    return -q if n < 0 else q

class DynamicQuantizeU8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        low = 0
        high = 0
        for i in range(8):
            value = s16(in_data >> (16*i))
            low = min(low,value)
            high = max(high,value)
        denominator = high-low
        if denominator == 0:
            denominator = 1
        zero = round_even_ratio(-255*low,denominator)
        result = (zero << 64) | (denominator << 72)
        for i in range(8):
            value = round_even_ratio(255*s16(in_data >> (16*i)),denominator)+zero
            value = min(255,max(0,value))
            result |= value << (8*i)
        return {"out_values": result}
